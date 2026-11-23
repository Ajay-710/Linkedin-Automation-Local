# LinkedIn + Omnichannel Outreach Automation (Local Gateway + n8n Cloud)

Automated LinkedIn + Email outbound system powered by **n8n Cloud**, **Google Sheets**, a local **Playwright LinkedIn browser service**, **Ollama (`gemma4:e2b`)**, and **ngrok**.

Each team member runs this lightweight local gateway on their own Mac or Windows laptop while sharing the central **n8n Cloud** workflow and **Google Sheets**.

---

## How It Works

```text
n8n Cloud Workflow (workflow/Boldest_Omnichannel_Outreach_Engine.json)
   │
   ├── Reads & writes prospects/output in Google Sheets
   ├── Calls SerpAPI for website fit signals & 60-day news triggers
   │
   └── Sends requests over ngrok ──► Local Gateway (http://127.0.0.1:3005)
                                         ├── /linkedin/action ──► Local Google Chrome (Playwright)
                                         │                        • Auto-detects Boldest mutual connections
                                         │                        • Likes recent post (warm-up)
                                         │                        • Sends connection note / direct DM
                                         │                        • Polls accepted requests at 9:30 AM
                                         │
                                         └── /api/* ───────────► Local Ollama (127.0.0.1:11434)
                                                                  • Model: gemma4:e2b
```

**Why one ngrok tunnel?** `linkedin-local-service/server.py` acts as a unified gateway on port `3005`: `/linkedin/action` runs browser automation in Chrome, and all `/api/*` requests proxy transparently to local Ollama (`127.0.0.1:11434`).

---

## Repository Structure

```text
Linkedin-Automation-Local/
├── linkedin-local-service/
│   ├── server.py               # FastAPI + Playwright + Ollama gateway (Port 3005)
│   └── requirements.txt        # Python dependencies
├── workflow/
│   └── Boldest_Omnichannel_Outreach_Engine.json  # Sanitized n8n Cloud workflow export
├── mac/
│   ├── setup_mac.command       # One-click Mac installer (Brew, Python, Ollama, gemma4:e2b, ngrok)
│   ├── login_linkedin.command  # One-time LinkedIn Chrome login on Mac
│   └── start_omnichannel.command  # Daily launcher (Ollama + Gateway + ngrok)
├── windows/
│   ├── setup_windows.bat       # Windows venv + dependencies + model pull
│   ├── login_linkedin.bat      # One-time LinkedIn Chrome login on Windows
│   └── start_omnichannel.bat   # Daily launcher (Ollama + Gateway + ngrok)
└── README.md
```

---

## Setup Guide: Brand-New Mac (Zero Prerequisites Installed)

### Option A — Automated One-Click Setup (Recommended)

1. Open **Terminal** (`Cmd + Space`, type `Terminal`, press `Enter`).
2. If Git isn't installed yet, macOS will prompt to install Apple Command Line Tools the first time you run `git`:
   ```bash
   git clone https://github.com/Ajay-710/Linkedin-Automation-Local.git
   cd Linkedin-Automation-Local
   ```
3. Run the Mac setup script (either double-click `mac/setup_mac.command` in Finder, or run from Terminal):
   ```bash
   chmod +x mac/*.command
   ./mac/setup_mac.command
   ```
   Wait for it to finish. It automatically installs **Homebrew**, **Google Chrome**, **Python 3**, **Ollama**, **ngrok**, creates `.venv`, installs Python packages, and downloads **`gemma4:e2b`**.

> **If macOS blocks double-clicking `.command` files:** Right-click the `.command` file → click **Open** → click **Open** in the dialog (or run `xattr -dr com.apple.quarantine mac/` once in Terminal).

---

### Option B — Manual Step-by-Step Mac Setup

1. **Install Homebrew** (if not installed):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   *(After install, follow the 2 lines Homebrew prints to add `/opt/homebrew/bin` to your shell `PATH` on Apple Silicon Macs.)*

2. **Install Google Chrome, Python 3, Ollama, and ngrok:**
   ```bash
   brew install --cask google-chrome
   brew install python ollama ngrok
   ```

3. **Download the Repo & Install Python Packages:**
   ```bash
   git clone https://github.com/Ajay-710/Linkedin-Automation-Local.git
   cd Linkedin-Automation-Local
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r linkedin-local-service/requirements.txt
   python -m playwright install chromium
   ```

4. **Start Ollama & Download the Local AI Model (`gemma4:e2b`):**
   Open a Terminal tab (or background process) to start Ollama, then pull the model:
   ```bash
   ollama serve &
   sleep 3
   ollama pull gemma4:e2b
   ```
   Verify the model is downloaded:
   ```bash
   ollama list
   ```
   You should see `gemma4:e2b` listed (~5 GB). *(Macs with 16 GB+ unified memory run it fastest; 8 GB Macs work best with other heavy apps closed.)*

5. **Connect Your Free ngrok Account (One-Time):**
   1. Sign up free at https://dashboard.ngrok.com/signup
   2. Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
   3. Run:
      ```bash
      ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
      ```
   4. **Pro Tip:** Free ngrok accounts include **1 static domain** under *Cloud Edge → Domains* in the ngrok dashboard. Claim it so your `https://....ngrok-free.app` URL never changes between restarts!

6. **One-Time LinkedIn Login on This Laptop:**
   ```bash
   ./mac/login_linkedin.command
   ```
   *(Or `source .venv/bin/activate && python linkedin-local-service/server.py --login`)*
   - A Chrome window opens to LinkedIn.
   - Log into **your** LinkedIn account (complete any 2FA/verification).
   - Once your LinkedIn Feed loads, **close the Chrome window**. Your session stays saved locally inside `linkedin-chrome-profile/` (gitignored — never pushed to GitHub).

7. **Daily Startup (Mac):**
   Double-click `mac/start_omnichannel.command` or run:
   ```bash
   ./mac/start_omnichannel.command
   ```
   *(If you claimed a static ngrok domain: `NGROK_DOMAIN=your-domain.ngrok-free.app ./mac/start_omnichannel.command`)*

---

## Setup Guide: Brand-New Windows Laptop (Zero Prerequisites Installed)

1. **Install the 4 Required Apps:**
   - **Git:** https://git-scm.com/download/win
   - **Google Chrome:** https://www.google.com/chrome/
   - **Python 3.10+:** https://www.python.org/downloads/ *(Check **"Add python.exe to PATH"** on the first installer screen!)*
   - **Ollama:** https://ollama.com/download/windows
   - **ngrok:** https://ngrok.com/download (or `winget install Ngrok.Ngrok`)

2. **Clone & Run Windows Setup:**
   Open **Command Prompt** or **PowerShell**:
   ```powershell
   git clone https://github.com/Ajay-710/Linkedin-Automation-Local.git
   cd Linkedin-Automation-Local
   windows\setup_windows.bat
   ```
   This creates `.venv`, installs `requirements.txt`, installs Playwright Chromium fallback, starts Ollama, and pulls **`gemma4:e2b`**.

3. **Connect ngrok (One-Time):**
   ```powershell
   ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
   ```

4. **One-Time LinkedIn Login:**
   Double-click **`windows\login_linkedin.bat`**, sign into your LinkedIn account, then close Chrome once your Feed loads.

5. **Daily Startup (Windows):**
   Double-click **`windows\start_omnichannel.bat`**. Three windows start (Ollama, Local LinkedIn Server on `:3005`, and ngrok tunnel). Copy the `https://....ngrok-free.dev` URL shown in the ngrok window.

---

## Connecting Your Laptop to n8n Cloud & Google Sheets

### 1. Import the Workflow into n8n Cloud
Import [`workflow/Boldest_Omnichannel_Outreach_Engine.json`](workflow/Boldest_Omnichannel_Outreach_Engine.json) into n8n and configure:
- **Google Sheets OAuth2 API credential:** Connect your Google account and select your Input/Output/Config spreadsheets.
- **SerpAPI Key:** Open both `Search Website Fit & Stack Signals` and `Search Recent News & Buying Triggers` nodes and replace `YOUR_SERPAPI_API_KEY` with your SerpAPI key.
- **Ollama Credential (`Ollama account`):** Set **Base URL** to your laptop's active ngrok `https://...` URL (no trailing slash).
- *(Optional)* **Smartlead:** Enable `Push Sequence to Smartlead Campaign` and set your campaign ID + API key if using email sync.

### 2. Google Sheets Structure
- **`Input` Tab (Prospects):**
  `Lead Status`, `First Name`, `Last Name`, `Job Title`, `Company Name`, `Company Name for Emails`, `Company Domain`, `Work Email`, `LinkedIn Profile`
  *(Rows marked `Omnichannel Sent`, `Completed`, `Rejected`, or `In Progress` are skipped automatically.)*
- **`Output` Tab:**
  `First Name`, `Last Name`, `Job Title`, `Company Name`, `Work Email`, `LinkedIn Profile`, `LinkedIn Score`, `LinkedIn Connection Note`, `LinkedIn DM 1 (Diagnostic)`, `LinkedIn DM 2 (Follow-up)`, `LinkedIn Status`, `Subject Line`, `Email 1`, `Email 2`, `Email 3`, `Dossier Dataasets`
- **`Config` Tab:**
  `LinkedInServerUrl` (or `NgrokUrl`) → set to the active teammate's `https://....ngrok-free.dev` URL.
  > **Important for Teammates:** Leave `li_at` and `BrowserState` **empty** in the `Config` sheet when running with `--login` on each laptop so one user's cookie does not overwrite another teammate's Chrome session.

### 3. Switching Between Teammates' Laptops
1. Teammate runs `mac/start_omnichannel.command` (or `windows/start_omnichannel.bat`).
2. Paste their ngrok `https://...` URL into:
   - `Config` Google Sheet → `LinkedInServerUrl` / `NgrokUrl`
   - n8n Cloud → `Ollama account` credential Base URL *(only needed if using their local Ollama; if you switch the two LLM nodes to Anthropic/OpenAI/Gemini, teammates don't need Ollama or credential edits at all)*.

---

## Automatic Mutual Connection Detection (Zero Manual List Needed)

Before generating copy, `LinkedIn: Check Mutual Connections` visits the prospect's profile with the active Chrome session:
1. Auto-discovers **Boldest** from the logged-in user's `/in/me/` profile (or LinkedIn company search) and caches employee slugs in `linkedin-local-service/boldest_roster_cache.json` (7-day TTL).
2. Checks the prospect's mutual connections filtered by Boldest's company ID and/or headline keywords (`"boldest"`).
3. If a mutual colleague is found (e.g., *Keith*), the connection note and DM automatically lead with `"I work with Keith"`. If none is found, standard role/fit personalization runs with zero placeholder leakage.

---

## Verify Everything Is Working

With `start_omnichannel` running, open a browser or run:
```bash
curl http://127.0.0.1:3005/health
```
Expected JSON:
```json
{
  "status": "ok",
  "service": "boldest-local-linkedin-playwright",
  "port": 3005
}
```

And test local Ollama directly:
```bash
curl http://127.0.0.1:3005/api/tags
```
You should see `gemma4:e2b` in the returned `models` list.

---

## Troubleshooting

- **`LinkedIn Session Required`:** Run `login_linkedin` again and sign in until your feed loads, then close Chrome.
- **Playwright opens Chrome slowly or times out (`25000ms`):** LinkedIn UI took long to hydrate or Chrome was already open using the same profile. Close extra automated Chrome windows and retry.
- **Ngrok error / n8n can't reach laptop:** Make sure `start_omnichannel` is still open, laptop is not asleep, and the URL in Google Sheets `Config` matches the current ngrok URL.
- **Mac says `"cannot be opened because it is from an unidentified developer"`:** Right-click the `.command` file → **Open**, or run `chmod +x mac/*.command && xattr -dr com.apple.quarantine mac/`.
- **Slow LLM or timeout on 8 GB laptop:** Keep background apps closed, or switch the two `Ollama Model` nodes in n8n to a hosted LLM (Anthropic/OpenAI/Gemini) while keeping Chrome Playwright local.
