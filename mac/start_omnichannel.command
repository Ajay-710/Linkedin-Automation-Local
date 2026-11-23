#!/bin/bash
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "===================================================="
echo " Starting BOLDEST Omnichannel Local Stack (macOS)"
echo " (Ollama + Playwright LinkedIn Gateway + Ngrok)"
echo "===================================================="

# 1. Start Ollama if not already running
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "[1/3] Starting Ollama..."
  nohup ollama serve >/tmp/boldest_ollama.log 2>&1 &
  sleep 3
else
  echo "[1/3] Ollama already running."
fi

# 2. Start Local LinkedIn Playwright + Ollama Gateway on Port 3005
echo "[2/3] Starting LinkedIn Playwright Gateway on port 3005..."
pkill -f "linkedin-local-service/server.py" 2>/dev/null || true
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/linkedin-local-service/server.py" >/tmp/boldest_linkedin_server.log 2>&1 &
else
  python3 "$REPO_ROOT/linkedin-local-service/server.py" >/tmp/boldest_linkedin_server.log 2>&1 &
fi
SERVER_PID=$!

for _ in {1..15}; do
  if curl -s http://127.0.0.1:3005/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[3/3] Starting Ngrok tunnel on port 3005..."
echo "Copy the https://... Forwarding URL below into your Google Config sheet (LinkedInServerUrl) & n8n Ollama credential."
echo "Keep this window open while running campaigns."
echo "===================================================="

if [ -n "${NGROK_DOMAIN:-}" ]; then
  ngrok http --domain="$NGROK_DOMAIN" 3005
else
  ngrok http 3005
fi
