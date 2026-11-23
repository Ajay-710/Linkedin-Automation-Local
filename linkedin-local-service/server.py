import asyncio
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ============================================================================
# CONFIGURATION
# ============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent
CHROME_PROFILE_DIR = BASE_DIR / "linkedin-chrome-profile"
CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
ROSTER_CACHE_PATH = Path(__file__).resolve().parent / "boldest_roster_cache.json"
ROSTER_TTL_SECONDS = 7 * 24 * 3600
ROSTER_RETRY_SECONDS = 6 * 3600

PORT = int(os.environ.get("LINKEDIN_SERVER_PORT", "3005"))
OLLAMA_UPSTREAM = os.environ.get("OLLAMA_UPSTREAM", "http://127.0.0.1:11434")
HEADLESS = os.environ.get("LINKEDIN_HEADLESS", "false").lower() in ("true", "1", "yes")
DEFAULT_DRY_RUN = os.environ.get("LINKEDIN_DRY_RUN", "false").lower() in ("true", "1", "yes")

app = FastAPI(title="BOLDEST Local LinkedIn Playwright + Ollama Gateway", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure only one Playwright task uses the persistent Chrome profile at a time
browser_lock = asyncio.Lock()


def human_delay(min_sec: float = 1.2, max_sec: float = 2.8) -> None:
    time.sleep(random.uniform(min_sec, max_sec))


def normalize_team_roster(boldest_team: Any) -> List[Dict[str, Any]]:
    """Normalizes Boldest team roster input (list, JSON string, or CSV) for mutual matching."""
    raw_items: List[Any] = []
    if isinstance(boldest_team, list):
        raw_items = boldest_team
    elif isinstance(boldest_team, str):
        s = boldest_team.strip()
        if s:
            if s.startswith("[") or s.startswith("{"):
                try:
                    parsed = json.loads(s)
                    raw_items = parsed if isinstance(parsed, list) else [parsed]
                except Exception:
                    raw_items = [part.strip() for part in s.split(",") if part.strip()]
            else:
                raw_items = [part.strip() for part in s.split(",") if part.strip()]

    roster: List[Dict[str, Any]] = []
    for entry in raw_items:
        if isinstance(entry, str):
            name = entry.strip()
            if not name:
                continue
            parts = name.split()
            roster.append({
                "name": name,
                "first_name": parts[0],
                "slug": "",
                "company": "boldest",
                "is_single_word": len(parts) == 1,
            })
        elif isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("full_name") or entry.get("first_name") or "").strip()
            first_name = str(entry.get("first_name") or (name.split()[0] if name else "")).strip()
            url = str(entry.get("linkedin_url") or entry.get("profile_url") or "").strip()
            slug = ""
            if "/in/" in url.lower():
                slug = url.lower().split("/in/", 1)[1].split("?", 1)[0].strip("/")
            if not name and not slug:
                continue
            parts = (name or first_name).split()
            roster.append({
                "name": name or first_name,
                "first_name": first_name or (parts[0] if parts else ""),
                "slug": slug,
                "company": str(entry.get("company") or "boldest").strip().lower(),
                "is_single_word": len(parts) <= 1 and not slug,
            })
    return roster


def normalize_company_keywords(company_keywords: Any) -> List[str]:
    """Normalizes partner company keywords (defaults to ['boldest'])."""
    raw: List[str] = []
    if isinstance(company_keywords, list):
        raw = [str(x).strip().lower() for x in company_keywords if str(x).strip()]
    elif isinstance(company_keywords, str):
        s = company_keywords.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    raw = [str(x).strip().lower() for x in parsed if str(x).strip()]
            except Exception:
                pass
        if not raw and s:
            raw = [p.strip().lower() for p in s.split(",") if p.strip()]
    return raw or ["boldest"]


def _extract_company_id_from_text(text: str) -> str:
    """Extracts numeric LinkedIn company ID from currentCompany links or URN HTML."""
    for pat in (
        r'currentCompany=(?:%5B|\[)(?:%22|")(\d+)',
        r'urn:li:(?:fsd_company|organization):(\d+)',
        r'"companyId"\s*:\s*(\d+)',
    ):
        m = re.search(pat, text or "", re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def _load_roster_cache() -> Dict[str, Any]:
    if not ROSTER_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(ROSTER_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_roster_cache(data: Dict[str, Any]) -> None:
    try:
        ROSTER_CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[LinkedIn] Could not write roster cache: {e}")


def _card_mentions_company(person: Dict[str, str], company_keywords: List[str]) -> bool:
    """Checks whether a LinkedIn card's headline/text mentions the partner company."""
    haystack = f"{person.get('headline', '')} {person.get('card_text', '')}".lower()
    for kw in company_keywords:
        if not kw:
            continue
        if re.search(r"\b" + re.escape(kw) + r"\b", haystack):
            return True
    return False


def _match_person_to_roster(
    person: Dict[str, str],
    roster: List[Dict[str, Any]],
    company_keywords: List[str],
) -> Optional[Dict[str, Any]]:
    """Matches a scraped LinkedIn mutual connection against synced company employees, headline keywords, or optional overrides."""
    p_name = (person.get("name") or "").strip()
    p_name_low = p_name.lower()
    p_slug = (person.get("slug") or "").strip().lower()
    p_words = [w.strip(".,()[]") for w in p_name_low.split() if w.strip(".,()[]")]

    # Priority 1: Exact LinkedIn profile slug match (auto-synced from company roster or optional override)
    for member in roster:
        if member["slug"] and p_slug and member["slug"] == p_slug:
            return {
                "colleague_name": p_name or member["name"],
                "colleague_first_name": member["first_name"] or (p_name.split()[0] if p_name else ""),
                "mutual_profile_url": person.get("profile_url", ""),
                "headline": person.get("headline", ""),
                "match_type": "company_roster_slug",
            }

    # Priority 2: Mutual's LinkedIn headline / card explicitly names Boldest
    if _card_mentions_company(person, company_keywords) and p_name:
        return {
            "colleague_name": p_name,
            "colleague_first_name": p_name.split()[0],
            "mutual_profile_url": person.get("profile_url", ""),
            "headline": person.get("headline", ""),
            "match_type": "headline_company_match",
        }

    # Priority 3: Multi-word full name match against synced company employees or optional override
    for member in roster:
        m_name_low = member["name"].lower()
        if not member["is_single_word"] and m_name_low and m_name_low in p_name_low:
            return {
                "colleague_name": p_name or member["name"],
                "colleague_first_name": member["first_name"],
                "mutual_profile_url": person.get("profile_url", ""),
                "headline": person.get("headline", ""),
                "match_type": "company_roster_name",
            }

    # Priority 4: Optional single-word override if user explicitly supplied one
    for member in roster:
        m_first = member["first_name"].lower()
        if member["is_single_word"] and m_first and p_words and p_words[0] == m_first:
            return {
                "colleague_name": p_name or member["name"],
                "colleague_first_name": member["first_name"],
                "mutual_profile_url": person.get("profile_url", ""),
                "headline": person.get("headline", ""),
                "match_type": "override_first_name",
            }
    return None


def _build_mutual_opener(matched_contacts: List[Dict[str, Any]]) -> str:
    """Formats a natural opener phrase such as 'I work with Keith'."""
    first_names: List[str] = []
    for c in matched_contacts:
        fn = (c.get("colleague_first_name") or "").strip()
        if fn and fn not in first_names:
            first_names.append(fn)
    if not first_names:
        return ""
    if len(first_names) == 1:
        return f"I work with {first_names[0]}"
    return f"I work with {first_names[0]} and {first_names[1]}"


def _extract_top_card_profile(page, top_card) -> Dict[str, Any]:
    """Reads connection degree, headline, and mutual connection summary from top profile section."""
    info = page.evaluate(
        """() => {
            const main = document.querySelector('main') || document.body;
            const sections = Array.from(main.querySelectorAll('section'));
            const top = sections[0] || main;
            const topText = (top.innerText || '').trim();
            const h1 = top.querySelector('h1');
            const name = h1 ? h1.innerText.trim() : '';

            // Headline typically sits in div.text-body-medium right under h1 block
            const headlineEl = top.querySelector('div.text-body-medium');
            const headline = headlineEl ? headlineEl.innerText.trim() : '';

            // Mutual connection link inside top card
            const allLinks = Array.from(top.querySelectorAll('a'));
            let mutualHref = '';
            let mutualText = '';
            for (const a of allLinks) {
                const href = a.getAttribute('href') || '';
                const txt = (a.innerText || '').replace(/\\s+/g, ' ').trim();
                if (
                    href.includes('connectionOf') ||
                    href.includes('facetConnectionOf') ||
                    txt.toLowerCase().includes('mutual connection')
                ) {
                    mutualHref = a.href || href;
                    mutualText = txt;
                    break;
                }
            }
            if (!mutualText) {
                const lines = topText.split('\\n').map(l => l.trim()).filter(Boolean);
                const hit = lines.find(l => l.toLowerCase().includes('mutual connection'));
                if (hit) mutualText = hit;
            }

            // About section text if present
            let about = '';
            for (const sec of sections) {
                const secText = (sec.innerText || '').trim();
                if (/^About\\b/i.test(secText)) {
                    about = secText.replace(/^About\\s*/i, '').trim().slice(0, 800);
                    break;
                }
            }

            return { name, headline, about, topText: topText.slice(0, 800), mutualHref, mutualText };
        }"""
    )
    return info or {}


def _scrape_mutual_search_page(page) -> List[Dict[str, str]]:
    """Extracts mutual connection people cards from LinkedIn's search results page."""
    raw_cards = page.evaluate(
        """() => {
            const main = document.querySelector('main') || document.body;
            const links = Array.from(main.querySelectorAll('a[href*="/in/"]'));
            const seen = new Set();
            const out = [];
            for (const a of links) {
                const rawHref = a.href || '';
                const match = rawHref.match(/linkedin\\.com\\/in\\/([^\\/?#]+)/i);
                if (!match) continue;
                const slug = match[1].toLowerCase();
                if (seen.has(slug)) continue;

                // Clean anchor text (skip image-only links)
                let text = (a.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!text || text.toLowerCase() === 'view' || text.length < 2) continue;
                // Remove "View X’s profile" or degree suffixes
                text = text
                    .replace(/View\\s+.*?profile/gi, '')
                    .replace(/[•·]\\s*(1st|2nd|3rd\\+?)\\b.*$/i, '')
                    .replace(/\\b(1st|2nd|3rd\\+?)\\s*degree connection\\b/gi, '')
                    .trim();
                if (!text || text.length < 2) continue;

                const card = a.closest('li, div[data-view-name], div.entity-result') || a.parentElement?.parentElement;
                const cardText = card ? (card.innerText || '').replace(/\\s+/g, ' ').trim() : text;
                const lines = card ? (card.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean) : [];
                const headline = lines.find(l =>
                    l !== text &&
                    !l.includes('degree connection') &&
                    !/^(Connect|Message|Follow|Pending|•\\s*1st)$/i.test(l) &&
                    l.length > 3
                ) || '';

                seen.add(slug);
                out.push({
                    name: text,
                    slug,
                    profile_url: `https://www.linkedin.com/in/${match[1]}/`,
                    headline,
                    card_text: cardText.slice(0, 500),
                });
            }
            return out;
        }"""
    )
    return raw_cards or []


def _discover_company_url(page, company_keywords: List[str]) -> str:
    """Auto-discovers the partner LinkedIn /company/ URL from Scott's profile or LinkedIn company search."""
    try:
        page.goto("https://www.linkedin.com/in/me/", wait_until="domcontentloaded")
        human_delay(2.0, 3.0)
        found = page.evaluate(
            """(keywords) => {
                const links = Array.from(document.querySelectorAll('main a[href*="/company/"]'));
                for (const a of links) {
                    const href = a.href || '';
                    const containerText = ((a.closest('section, li, div') || a).innerText || '').toLowerCase();
                    if (keywords.some(k => containerText.includes(k) || href.toLowerCase().includes(k))) {
                        return href.split('?')[0];
                    }
                }
                return '';
            }""",
            company_keywords,
        )
        if found:
            return found
    except Exception as e:
        print(f"[LinkedIn] Auto-discover from /in/me/ warning: {e}")

    # Fallback: search LinkedIn Companies for the primary keyword (e.g. "boldest")
    try:
        primary_kw = company_keywords[0]
        page.goto(f"https://www.linkedin.com/search/results/companies/?keywords={primary_kw}", wait_until="domcontentloaded")
        human_delay(2.0, 3.0)
        found = page.evaluate(
            """(kw) => {
                const links = Array.from(document.querySelectorAll('main a[href*="/company/"]'));
                for (const a of links) {
                    const txt = (a.innerText || '').trim().toLowerCase();
                    if (txt === kw || txt.startsWith(kw + ' ')) {
                        return (a.href || '').split('?')[0];
                    }
                }
                return '';
            }""",
            primary_kw,
        )
        if found:
            return found
    except Exception as e:
        print(f"[LinkedIn] Auto-discover from company search warning: {e}")
    return ""


def ensure_company_roster(
    page,
    company_keywords: List[str],
    company_url: str = "",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Automatically syncs and caches Boldest's employee roster + LinkedIn company_id without manual per-employee setup."""
    cache = _load_roster_cache()
    now = time.time()
    ttl = ROSTER_TTL_SECONDS if (cache.get("company_id") or cache.get("employees")) else ROSTER_RETRY_SECONDS
    if not force_refresh and cache and (now - float(cache.get("updated_at", 0))) < ttl:
        if cache.get("company_keywords") == company_keywords and (not company_url or cache.get("company_url") == company_url):
            return cache

    target_company_url = (company_url or "").strip() or cache.get("company_url") or _discover_company_url(page, company_keywords)
    company_id = str(cache.get("company_id") or "")
    employees: Dict[str, Dict[str, str]] = {}

    if target_company_url:
        try:
            people_url = target_company_url.rstrip("/") + "/people/"
            page.goto(people_url, wait_until="domcontentloaded")
            human_delay(2.2, 3.4)
            html = page.content()
            found_id = _extract_company_id_from_text(html)
            if found_id:
                company_id = found_id
            for _ in range(2):
                page.mouse.wheel(0, 700)
                human_delay(0.8, 1.3)
            for p in _scrape_mutual_search_page(page):
                if p.get("slug"):
                    employees[p["slug"]] = {
                        "name": p.get("name", ""),
                        "slug": p["slug"],
                        "profile_url": p.get("profile_url", ""),
                        "headline": p.get("headline", ""),
                    }
        except Exception as e:
            print(f"[LinkedIn] Company /people/ sync warning: {e}")

    if company_id:
        try:
            for page_idx in range(1, 4):  # Sync up to ~30 current employees automatically
                search_url = (
                    f"https://www.linkedin.com/search/results/people/"
                    f"?currentCompany=%5B%22{company_id}%22%5D&page={page_idx}"
                )
                page.goto(search_url, wait_until="domcontentloaded")
                human_delay(2.0, 3.0)
                cards = _scrape_mutual_search_page(page)
                if not cards:
                    break
                for p in cards:
                    if p.get("slug"):
                        employees[p["slug"]] = {
                            "name": p.get("name", ""),
                            "slug": p["slug"],
                            "profile_url": p.get("profile_url", ""),
                            "headline": p.get("headline", ""),
                        }
                if page.locator("button[aria-label='Next']:not([disabled])").count() == 0:
                    break
        except Exception as e:
            print(f"[LinkedIn] Company currentCompany search sync warning: {e}")

    new_cache = {
        "company_url": target_company_url,
        "company_id": company_id,
        "company_keywords": company_keywords,
        "employees": list(employees.values()),
        "updated_at": now,
    }
    _save_roster_cache(new_cache)
    return new_cache


def _set_current_company_filter(search_url: str, company_id: str) -> str:
    """Appends or replaces LinkedIn's currentCompany filter on a mutual connections search URL."""
    cleaned = re.sub(r"([&?])currentCompany=[^&]*", r"\1", search_url).rstrip("?&")
    sep = "&" if "?" in cleaned else "?"
    return f"{cleaned}{sep}currentCompany=%5B%22{company_id}%22%5D"


def detect_mutual_connections(
    page,
    top_info: Dict[str, Any],
    manual_roster: List[Dict[str, Any]],
    company_cache: Dict[str, Any],
    company_keywords: List[str],
) -> Dict[str, Any]:
    """Detects Boldest colleague mutual connections automatically from synced company data and mutual search cards."""
    mutual_text = (top_info.get("mutualText") or "").strip()
    mutual_href = (top_info.get("mutualHref") or "").strip()

    if not mutual_text and not mutual_href:
        return {
            "has_boldest_mutual": False,
            "mutual_text": "",
            "mutual_connections_sampled": [],
            "matched_boldest_contacts": [],
            "lead_colleague_names": [],
            "mutual_opener_phrase": "",
        }

    # Combine auto-synced company employees + any optional manual overrides
    synced_entries = [
        {"name": e.get("name", ""), "linkedin_url": e.get("profile_url", "")}
        for e in (company_cache.get("employees") or [])
    ]
    combined_roster = normalize_team_roster(synced_entries) + manual_roster
    company_id = str(company_cache.get("company_id") or "").strip()

    # Quick check: banner directly names someone in synced roster / manual override
    banner_matches: List[Dict[str, Any]] = []
    if mutual_text and combined_roster:
        banner_low = mutual_text.lower()
        for member in combined_roster:
            m_name = member["name"].lower()
            m_first = member["first_name"].lower()
            hit = (not member["is_single_word"] and m_name in banner_low) or (
                member["is_single_word"] and any(tok.strip(",.") == m_first for tok in banner_low.split())
            )
            if hit:
                banner_matches.append({
                    "colleague_name": member["name"],
                    "colleague_first_name": member["first_name"],
                    "mutual_profile_url": f"https://www.linkedin.com/in/{member['slug']}/" if member["slug"] else "",
                    "headline": "",
                    "match_type": "profile_banner",
                })

    has_hidden_others = "other mutual" in mutual_text.lower() or "others" in mutual_text.lower()
    sampled_people: List[Dict[str, str]] = []
    matched: List[Dict[str, Any]] = []

    def _collect_matches(cards: List[Dict[str, str]], filtered_by_company_pill: bool = False) -> None:
        for person in cards:
            m = _match_person_to_roster(person, combined_roster, company_keywords)
            if not m and filtered_by_company_pill and person.get("name"):
                # Verify LinkedIn's active filter pill in DOM actually selected the company before trusting filtered results
                m = {
                    "colleague_name": person["name"],
                    "colleague_first_name": person["name"].split()[0],
                    "mutual_profile_url": person.get("profile_url", ""),
                    "headline": person.get("headline", ""),
                    "match_type": "linkedin_company_filter",
                }
            if m and not any(
                x["colleague_first_name"].lower() == m["colleague_first_name"].lower()
                and x["mutual_profile_url"] == m["mutual_profile_url"]
                for x in matched
            ):
                matched.append(m)

    if mutual_href and (has_hidden_others or not banner_matches):
        try:
            base_mutual_url = mutual_href if mutual_href.startswith("http") else f"https://www.linkedin.com{mutual_href}"

            # Pass 1: If Boldest company_id is known, filter ALL mutual connections directly by currentCompany
            if company_id:
                filtered_url = _set_current_company_filter(base_mutual_url, company_id)
                page.goto(filtered_url, wait_until="domcontentloaded")
                human_delay(2.0, 3.0)
                pill_active = page.evaluate(
                    """(keywords) => {
                        const pills = Array.from(document.querySelectorAll('button[aria-pressed="true"], button.artdeco-pill--selected'));
                        return pills.some(b => {
                            const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                            return keywords.some(k => t.includes(k)) || t.includes('current company');
                        });
                    }""",
                    company_keywords,
                )
                cards = _scrape_mutual_search_page(page)
                if cards:
                    sampled_people.extend(cards)
                    _collect_matches(cards, filtered_by_company_pill=bool(pill_active))

            # Pass 2: Fallback unfiltered mutuals pages (matches synced roster + headline "Boldest" keyword)
            if not matched:
                for page_idx in range(1, 3):
                    paged_url = base_mutual_url if page_idx == 1 else f"{base_mutual_url}{'&' if '?' in base_mutual_url else '?'}page={page_idx}"
                    page.goto(paged_url, wait_until="domcontentloaded")
                    human_delay(2.0, 3.2)
                    cards = _scrape_mutual_search_page(page)
                    if not cards:
                        break
                    sampled_people.extend(cards)
                    _collect_matches(cards, filtered_by_company_pill=False)
                    if matched or page.locator("button[aria-label='Next']:not([disabled])").count() == 0:
                        break
        except Exception as e:
            print(f"[LinkedIn] Mutual connections search warning: {e}")

    if not matched and banner_matches:
        matched = banner_matches

    lead_names: List[str] = []
    for c in matched:
        fn = (c.get("colleague_first_name") or "").strip()
        if fn and fn not in lead_names:
            lead_names.append(fn)

    return {
        "has_boldest_mutual": len(matched) > 0,
        "mutual_text": mutual_text,
        "mutual_connections_sampled": [
            {"name": p.get("name", ""), "headline": p.get("headline", ""), "profile_url": p.get("profile_url", "")}
            for p in sampled_people[:15]
        ],
        "matched_boldest_contacts": matched,
        "lead_colleague_names": lead_names,
        "mutual_opener_phrase": _build_mutual_opener(matched),
        "company_roster_count": len(company_cache.get("employees") or []),
    }


def inject_cookies_if_provided(context, cookie_str: str, browser_state_str: str) -> None:
    """Injects li_at cookie or browserState cookies from n8n Config Sheet if present."""
    cookies_to_add = []

    clean_cookie = (cookie_str or "").strip()
    if clean_cookie and clean_cookie not in ("YOUR_LI_AT_COOKIE", "undefined", "null"):
        cookies_to_add.append({
            "name": "li_at",
            "value": clean_cookie,
            "domain": ".linkedin.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
        })

    clean_state = (browser_state_str or "").strip()
    if clean_state and clean_state.startswith("{") or clean_state.startswith("["):
        try:
            parsed = json.loads(clean_state)
            state_cookies = parsed.get("cookies", parsed) if isinstance(parsed, dict) else parsed
            if isinstance(state_cookies, list):
                for c in state_cookies:
                    if isinstance(c, dict) and c.get("name") and c.get("value"):
                        cookies_to_add.append({
                            "name": c["name"],
                            "value": c["value"],
                            "domain": c.get("domain", ".linkedin.com"),
                            "path": c.get("path", "/"),
                        })
        except Exception:
            pass

    if cookies_to_add:
        try:
            context.add_cookies(cookies_to_add)
        except Exception as e:
            print(f"[LinkedIn] Cookie injection warning: {e}")


def send_dm_via_top_card(page, top_card, dm_text: str, profile_url: str, success_status: str) -> Dict[str, Any]:
    """Closes stale chat overlays, opens the prospect's active DM bubble, and sends the message."""
    # Dismiss any pre-existing minimized/open chat bubbles so we never target an off-screen textbox
    close_btns = page.locator(
        "div.msg-overlay-conversation-bubble button[aria-label*='Close'], "
        "div.msg-overlay-conversation-bubble button:has-text('Close'), "
        "div.msg-overlay-bubble-header__controls button:has(svg[data-test-icon='close-small'])"
    )
    for _ in range(min(close_btns.count(), 5)):
        try:
            btn = close_btns.first
            if btn.is_visible():
                btn.click(timeout=2000)
                human_delay(0.3, 0.5)
        except Exception:
            break

    msg_btn = top_card.locator(
        "button:has-text('Message'), a:has-text('Message')"
    ).first
    if msg_btn.count() == 0 or not msg_btn.is_visible():
        return {
            "success": False,
            "status": "Message Button Not Available",
            "message": "Could not find Message button on profile",
            "url": profile_url,
        }

    msg_btn.click()
    human_delay(1.8, 2.6)

    # Target only an expanded, active conversation bubble (never a minimized off-screen one)
    active_bubble = page.locator(
        "div.msg-overlay-conversation-bubble:not(.msg-overlay-conversation-bubble--is-minimized)"
    ).last
    if active_bubble.count() == 0:
        active_bubble = page

    msg_box = active_bubble.locator(
        "div.msg-form__contenteditable[contenteditable='true'], "
        "div[role='textbox'][aria-label*='Write a message']"
    ).last
    if msg_box.count() == 0:
        return {
            "success": False,
            "status": "Message Box Not Found",
            "message": "Message overlay did not open a compose textbox",
            "url": profile_url,
        }

    msg_box.click()
    human_delay(0.5, 1.0)
    msg_box.fill(dm_text)
    human_delay(1.0, 1.8)

    send_dm_btn = active_bubble.locator(
        "button.msg-form__send-button, button[type='submit']:has-text('Send')"
    ).last
    if send_dm_btn.count() > 0 and send_dm_btn.is_enabled():
        send_dm_btn.click()
        human_delay(1.5, 2.5)
        return {
            "success": True,
            "status": success_status,
            "message": "Successfully sent LinkedIn DM",
            "url": profile_url,
        }

    return {
        "success": False,
        "status": "DM Send Button Disabled",
        "message": "Typed DM but Send button was not enabled",
        "url": profile_url,
    }


def is_logged_in(page) -> bool:
    """Checks if the current LinkedIn session is authenticated."""
    url = page.url.lower()
    if any(k in url for k in ("/login", "/uas/login", "/authwall", "checkpoint/challenge")):
        return False
    # Check if sign-in button is prominent or nav bar is present
    try:
        if page.locator("#global-nav, .global-nav__content, nav[aria-label*='Primary']").count() > 0:
            return True
        if page.locator("a[href*='/login'], button:has-text('Sign in')").count() > 0:
            return False
    except Exception:
        pass
    return True


def execute_playwright_action(
    action: str,
    profile_url: str,
    message: str = "",
    dm_message: str = "",
    cookie: str = "",
    browser_state: str = "",
    boldest_team: Any = None,
    company_keywords: Any = None,
    company_url: str = "",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Runs the requested LinkedIn action synchronously inside local Chrome."""
    action = (action or "").strip().upper()
    profile_url = (profile_url or "").strip()
    manual_roster = normalize_team_roster(boldest_team)
    kw_list = normalize_company_keywords(company_keywords)

    if action != "SYNC_COMPANY_TEAM" and (not profile_url or "linkedin.com/in/" not in profile_url.lower()):
        return {
            "success": False,
            "status": "Skipped (No Valid LinkedIn URL)",
            "message": f"Invalid or empty LinkedIn profile URL: '{profile_url}'",
            "url": profile_url,
            "has_boldest_mutual": False,
            "matched_boldest_contacts": [],
            "lead_colleague_names": [],
            "mutual_opener_phrase": "",
        }

    if dry_run or DEFAULT_DRY_RUN:
        print(f"[DRY-RUN] Action={action} | URL={profile_url} | Message={message[:80]!r}")
        status_map = {
            "LIKE_RECENT_POSTS": "Liked Recent Post (Dry Run)",
            "SEND_CONNECTION_REQUEST": "Connection Sent",
            "CHECK_CONNECTION_STATUS": "connected",
            "CHECK_MUTUAL_CONNECTIONS": "mutual_checked",
            "INSPECT_PROFILE": "mutual_checked",
            "SYNC_COMPANY_TEAM": "company_synced",
            "SEND_DM": "Connected - Diagnostic DM Sent",
        }
        return {
            "success": True,
            "dryRun": True,
            "action": action,
            "status": status_map.get(action, "Success"),
            "connected": action == "CHECK_CONNECTION_STATUS",
            "connection_degree": "2nd",
            "has_boldest_mutual": False,
            "matched_boldest_contacts": [],
            "lead_colleague_names": [],
            "mutual_opener_phrase": "",
            "message": f"Simulated {action} in Dry-Run mode",
            "url": profile_url,
        }

    print(f"[LinkedIn] Starting {action} on {profile_url} (Headless={HEADLESS})")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            channel="chrome",
            headless=HEADLESS,
            viewport={"width": 1366, "height": 860},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--disable-infobars",
            ],
        )
        try:
            inject_cookies_if_provided(context, cookie, browser_state)
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(25000)

            # ----------------------------------------------------------------
            # 1. LIKE_RECENT_POSTS
            # ----------------------------------------------------------------
            if action == "LIKE_RECENT_POSTS":
                activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
                page.goto(activity_url, wait_until="domcontentloaded")
                human_delay(2.5, 4.0)

                if not is_logged_in(page):
                    return {
                        "success": False,
                        "status": "LinkedIn Session Required",
                        "message": "Not logged into LinkedIn. Run login_linkedin.bat or provide li_at cookie.",
                        "url": profile_url,
                    }

                page.mouse.wheel(0, 450)
                human_delay(1.2, 2.2)

                like_selector = (
                    "button[aria-label='Reaction button state: no reaction'], "
                    "button.react-button__trigger[aria-pressed='false'], "
                    "button[aria-label*='React Like'][aria-pressed='false']"
                )
                like_buttons = page.locator(like_selector)
                if like_buttons.count() > 0:
                    like_buttons.first.scroll_into_view_if_needed()
                    human_delay(0.8, 1.5)
                    like_buttons.first.click()
                    human_delay(1.5, 2.5)
                    return {
                        "success": True,
                        "status": "Liked Recent Post",
                        "message": "Successfully liked prospect's recent post",
                        "url": profile_url,
                    }
                return {
                    "success": True,
                    "status": "Profile Warmed Up (All Recent Posts Already Liked)",
                    "message": "Visited profile activity; recent posts are already liked",
                    "url": profile_url,
                }

            # ----------------------------------------------------------------
            # 2. SEND_CONNECTION_REQUEST
            # ----------------------------------------------------------------
            elif action == "SEND_CONNECTION_REQUEST":
                page.goto(profile_url, wait_until="domcontentloaded")
                human_delay(2.5, 4.0)

                if not is_logged_in(page):
                    return {
                        "success": False,
                        "status": "LinkedIn Session Required",
                        "message": "Not logged into LinkedIn. Run login_linkedin.bat or provide li_at cookie.",
                        "url": profile_url,
                    }

                top_card = page.locator("main section").first
                top_text = ""
                try:
                    top_text = top_card.inner_text()[:500]
                except Exception:
                    pass

                is_first_deg = (
                    "· 1st" in top_text
                    or "• 1st" in top_text
                    or "\n1st\n" in top_text
                    or top_card.locator("span.dist-value:has-text('1st')").count() > 0
                )

                # If already 1st-degree connected, send the DM text directly instead of a connection note
                if is_first_deg:
                    direct_dm = (dm_message or message or "").strip()
                    if direct_dm:
                        return send_dm_via_top_card(
                            page=page,
                            top_card=top_card,
                            dm_text=direct_dm,
                            profile_url=profile_url,
                            success_status="Connected - Diagnostic DM Sent",
                        )
                    return {
                        "success": True,
                        "status": "Connected - Already 1st Degree",
                        "message": "Prospect is already a 1st-degree connection",
                        "url": profile_url,
                    }

                # Check if already Pending in top profile card
                if top_card.locator("button:has-text('Pending'), button[aria-label*='Pending']").count() > 0:
                    return {
                        "success": True,
                        "status": "Connection Sent",
                        "message": "Connection request is already Pending",
                        "url": profile_url,
                    }

                # Look for direct Connect button in top profile card only
                connect_btn = top_card.locator(
                    "button[aria-label*='Invite'][aria-label*='to connect'], "
                    "button:has-text('Connect'), a:has-text('Connect')"
                ).first

                clicked_connect = False
                if connect_btn.count() > 0 and connect_btn.is_visible():
                    connect_btn.click()
                    clicked_connect = True
                else:
                    # Check inside "More" dropdown menu in top profile card
                    more_btn = top_card.locator(
                        "button[aria-label='More'], button[aria-label='More actions'], button:has-text('More')"
                    ).first
                    if more_btn.count() > 0 and more_btn.is_visible():
                        more_btn.click()
                        human_delay(0.8, 1.4)
                        dropdown_connect = page.locator(
                            "div.artdeco-dropdown__content div[role='button']:has-text('Connect'), "
                            "div.artdeco-dropdown__content span:has-text('Connect')"
                        ).first
                        if dropdown_connect.count() > 0 and dropdown_connect.is_visible():
                            dropdown_connect.click()
                            clicked_connect = True

                if not clicked_connect:
                    return {
                        "success": False,
                        "status": "Connect Button Not Found",
                        "message": "Could not locate Connect option on profile",
                        "url": profile_url,
                    }

                human_delay(1.0, 1.8)

                # Handle "Add a note" modal
                note_text = (message or "").strip()[:285]
                if note_text:
                    add_note_btn = page.locator(
                        "button[aria-label='Add a note'], button:has-text('Add a note')"
                    ).first
                    if add_note_btn.count() > 0 and add_note_btn.is_visible():
                        add_note_btn.click()
                        human_delay(0.6, 1.2)
                        textarea = page.locator(
                            "textarea[name='message'], textarea#custom-message, textarea"
                        ).first
                        if textarea.count() > 0:
                            textarea.fill(note_text)
                            human_delay(0.8, 1.5)

                send_btn = page.locator(
                    "button[aria-label='Send invitation'], "
                    "button[aria-label='Send now'], "
                    "div[role='dialog'] button:has-text('Send')"
                ).first
                if send_btn.count() > 0 and send_btn.is_visible():
                    send_btn.click()
                    human_delay(1.5, 2.5)
                    return {
                        "success": True,
                        "status": "Connection Sent",
                        "message": "Sent personalized connection request",
                        "url": profile_url,
                    }

                return {
                    "success": False,
                    "status": "Modal Send Button Not Found",
                    "message": "Opened connect dialog but could not click Send",
                    "url": profile_url,
                }

            # ----------------------------------------------------------------
            # 3. CHECK_CONNECTION_STATUS
            # ----------------------------------------------------------------
            elif action == "CHECK_CONNECTION_STATUS":
                page.goto(profile_url, wait_until="domcontentloaded")
                human_delay(2.5, 3.8)

                if not is_logged_in(page):
                    return {
                        "success": False,
                        "status": "not_authenticated",
                        "connected": False,
                        "url": profile_url,
                    }

                top_card = page.locator("main section").first
                top_text = ""
                try:
                    top_text = top_card.inner_text()[:500]
                except Exception:
                    pass

                is_first_degree = (
                    "· 1st" in top_text
                    or "• 1st" in top_text
                    or "\n1st\n" in top_text
                    or top_card.locator("span.dist-value:has-text('1st')").count() > 0
                )
                is_pending = (
                    top_card.locator("button:has-text('Pending'), button[aria-label*='Pending']").count() > 0
                )

                if is_first_degree:
                    company_cache = ensure_company_roster(page, kw_list, company_url=company_url)
                    top_info = _extract_top_card_profile(page, top_card)
                    mutual_data = detect_mutual_connections(
                        page, top_info, manual_roster, company_cache, kw_list
                    )
                    return {
                        "success": True,
                        "status": "connected",
                        "connected": True,
                        "url": profile_url,
                        **mutual_data,
                    }
                elif is_pending:
                    return {
                        "success": True,
                        "status": "pending",
                        "connected": False,
                        "has_boldest_mutual": False,
                        "matched_boldest_contacts": [],
                        "lead_colleague_names": [],
                        "mutual_opener_phrase": "",
                        "url": profile_url,
                    }
                return {
                    "success": True,
                    "status": "not_connected",
                    "connected": False,
                    "has_boldest_mutual": False,
                    "matched_boldest_contacts": [],
                    "lead_colleague_names": [],
                    "mutual_opener_phrase": "",
                    "url": profile_url,
                }

            # ----------------------------------------------------------------
            # 3b. CHECK_MUTUAL_CONNECTIONS / INSPECT_PROFILE / SYNC_COMPANY_TEAM
            # ----------------------------------------------------------------
            elif action == "SYNC_COMPANY_TEAM":
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
                human_delay(1.8, 2.5)
                if not is_logged_in(page):
                    return {
                        "success": False,
                        "status": "LinkedIn Session Required",
                        "message": "Not logged into LinkedIn. Run login_linkedin.bat or provide li_at cookie.",
                    }
                cache = ensure_company_roster(page, kw_list, company_url=company_url, force_refresh=True)
                return {
                    "success": True,
                    "status": "company_synced",
                    "company_url": cache.get("company_url", ""),
                    "company_id": cache.get("company_id", ""),
                    "employees_cached": len(cache.get("employees") or []),
                }

            elif action in ("CHECK_MUTUAL_CONNECTIONS", "INSPECT_PROFILE"):
                page.goto(profile_url, wait_until="domcontentloaded")
                human_delay(2.5, 3.8)

                if not is_logged_in(page):
                    return {
                        "success": False,
                        "status": "LinkedIn Session Required",
                        "message": "Not logged into LinkedIn. Run login_linkedin.bat or provide li_at cookie.",
                        "has_boldest_mutual": False,
                        "matched_boldest_contacts": [],
                        "lead_colleague_names": [],
                        "mutual_opener_phrase": "",
                        "url": profile_url,
                    }

                top_card = page.locator("main section").first
                top_info = _extract_top_card_profile(page, top_card)
                top_text = top_info.get("topText") or ""

                is_first_degree = (
                    "· 1st" in top_text
                    or "• 1st" in top_text
                    or "\n1st\n" in top_text
                    or top_card.locator("span.dist-value:has-text('1st')").count() > 0
                )
                is_pending = (
                    top_card.locator("button:has-text('Pending'), button[aria-label*='Pending']").count() > 0
                )

                company_cache = ensure_company_roster(page, kw_list, company_url=company_url)
                mutual_data = detect_mutual_connections(
                    page, top_info, manual_roster, company_cache, kw_list
                )
                return {
                    "success": True,
                    "status": "mutual_checked",
                    "url": profile_url,
                    "profile_name": top_info.get("name", ""),
                    "headline": top_info.get("headline", ""),
                    "about": top_info.get("about", ""),
                    "connection_degree": "1st" if is_first_degree else ("pending" if is_pending else "2nd"),
                    "is_connected": is_first_degree,
                    "is_pending": is_pending,
                    **mutual_data,
                }

            # ----------------------------------------------------------------
            # 4. SEND_DM
            # ----------------------------------------------------------------
            elif action == "SEND_DM":
                page.goto(profile_url, wait_until="domcontentloaded")
                human_delay(2.5, 4.0)

                if not is_logged_in(page):
                    return {
                        "success": False,
                        "status": "LinkedIn Session Required",
                        "message": "Not logged into LinkedIn",
                        "url": profile_url,
                    }

                top_card = page.locator("main section").first
                dm_text = (message or "").strip() or "Hey! Quick question: how are you currently handling sizing and fit guidance across your product pages?"
                return send_dm_via_top_card(
                    page=page,
                    top_card=top_card,
                    dm_text=dm_text,
                    profile_url=profile_url,
                    success_status="Connected - Diagnostic DM Sent",
                )

            else:
                return {
                    "success": False,
                    "status": f"Unknown Action: {action}",
                    "message": "Supported actions: LIKE_RECENT_POSTS, SEND_CONNECTION_REQUEST, CHECK_CONNECTION_STATUS, CHECK_MUTUAL_CONNECTIONS, INSPECT_PROFILE, SEND_DM",
                    "url": profile_url,
                }

        except PlaywrightTimeoutError as te:
            return {
                "success": False,
                "status": "LinkedIn Timeout",
                "message": str(te),
                "url": profile_url,
            }
        except Exception as e:
            return {
                "success": False,
                "status": "LinkedIn Error",
                "message": str(e),
                "url": profile_url,
            }
        finally:
            try:
                context.close()
            except Exception:
                pass


# ============================================================================
# FASTAPI ROUTES
# ============================================================================
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "boldest-local-linkedin-playwright",
        "port": PORT,
        "chrome_profile": str(CHROME_PROFILE_DIR),
        "headless": HEADLESS,
        "default_dry_run": DEFAULT_DRY_RUN,
        "ollama_upstream": OLLAMA_UPSTREAM,
    }


@app.post("/linkedin/action")
async def run_linkedin_action(request: Request):
    body = await request.json()
    action = body.get("action", "")
    urls = body.get("urls") or []
    profile_url = urls[0] if isinstance(urls, list) and len(urls) > 0 else body.get("url", "")
    message = body.get("message", "")
    dm_message = body.get("dm_message", "")
    cookie = body.get("cookie", "")
    browser_state = body.get("browserState", "")
    boldest_team = body.get("boldest_team", body.get("boldestTeam", None))
    company_keywords = body.get("company_keywords", body.get("companyKeywords", None))
    company_url = body.get("company_url", body.get("companyUrl", ""))
    dry_run = bool(body.get("dryRun", DEFAULT_DRY_RUN))

    async with browser_lock:
        result = await asyncio.to_thread(
            execute_playwright_action,
            action=action,
            profile_url=profile_url,
            message=message,
            dm_message=dm_message,
            cookie=cookie,
            browser_state=browser_state,
            boldest_team=boldest_team,
            company_keywords=company_keywords,
            company_url=company_url,
            dry_run=dry_run,
        )

    # Return an array matching the Apify dataset item structure expected by n8n
    return [result]


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def proxy_to_ollama(request: Request, path: str):
    """
    Transparently proxies all non-/linkedin requests (e.g., /api/chat, /api/tags, /v1/*)
    to local Ollama (127.0.0.1:11434) so a SINGLE ngrok tunnel can serve both
    Ollama and the Local LinkedIn Playwright service!
    """
    target_url = f"{OLLAMA_UPSTREAM}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    body = await request.body()
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding", "connection")
    }

    # For /api/chat and /api/generate, force non-streaming single-line JSON so
    # LangChain/n8n never hits chunk-boundary JSON split errors on thinking models
    if request.method == "POST" and path.strip("/") in ("api/chat", "api/generate") and body:
        try:
            payload = json.loads(body.decode("utf-8"))
            payload["stream"] = False
            body = json.dumps(payload).encode("utf-8")
        except Exception:
            pass

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                content=body,
                headers=headers,
            )
            resp_headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower() not in ("transfer-encoding", "content-length", "content-encoding", "connection")
            }

            if request.method == "POST" and path.strip("/") in ("api/chat", "api/generate"):
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        data.pop("thinking", None)
                        if isinstance(data.get("message"), dict):
                            data["message"].pop("thinking", None)
                        clean_bytes = (json.dumps(data) + "\n").encode("utf-8")
                        return Response(
                            content=clean_bytes,
                            status_code=resp.status_code,
                            headers=resp_headers,
                            media_type="application/x-ndjson",
                        )
                except Exception:
                    pass

            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=resp_headers,
            )
        except Exception as e:
            return Response(
                content=json.dumps({"error": f"Ollama proxy error: {str(e)}"}),
                status_code=502,
                media_type="application/json",
            )


def interactive_login():
    """Opens local Chrome with the persistent profile so the user can sign into LinkedIn once."""
    print("====================================================")
    print(" Opening Chrome for One-Time LinkedIn Login...")
    print(f" Profile Directory: {CHROME_PROFILE_DIR}")
    print(" Log into your LinkedIn account, then close Chrome.")
    print("====================================================")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            channel="chrome",
            headless=False,
            viewport={"width": 1366, "height": 860},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.linkedin.com/feed/")
        try:
            # Wait until user closes the browser window
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        finally:
            try:
                context.close()
            except Exception:
                pass
    print("[SUCCESS] LinkedIn session saved to persistent Chrome profile!")


if __name__ == "__main__":
    if "--login" in sys.argv:
        interactive_login()
    else:
        print("====================================================")
        print(f" BOLDEST Local LinkedIn + Ollama Gateway")
        print(f" LinkedIn Endpoint: http://localhost:{PORT}/linkedin/action")
        print(f" Ollama Proxy:      http://localhost:{PORT}/api/* -> {OLLAMA_UPSTREAM}")
        print(f" Health Check:      http://localhost:{PORT}/health")
        print("====================================================")
        uvicorn.run(app, host="0.0.0.0", port=PORT)
