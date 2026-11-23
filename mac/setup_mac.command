#!/bin/bash
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Ensure Homebrew paths are available on both Apple Silicon and Intel Macs
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "===================================================="
echo " BOLDEST Omnichannel Automation - Brand New Mac Setup"
echo "===================================================="

if ! command -v brew >/dev/null 2>&1; then
  echo "[1/6] Installing Homebrew (macOS package manager)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
else
  echo "[1/6] Homebrew already installed."
fi

echo "[2/6] Checking Google Chrome, Python 3, Ollama, and ngrok..."
if [ ! -d "/Applications/Google Chrome.app" ]; then
  echo "Installing Google Chrome..."
  brew install --cask google-chrome
fi
command -v python3 >/dev/null 2>&1 || brew install python
command -v ollama >/dev/null 2>&1 || brew install ollama
command -v ngrok >/dev/null 2>&1 || brew install ngrok

echo "[3/6] Creating Python virtual environment and installing dependencies..."
python3 -m venv "$REPO_ROOT/.venv"
source "$REPO_ROOT/.venv/bin/activate"
pip install --upgrade pip
pip install -r "$REPO_ROOT/linkedin-local-service/requirements.txt"

echo "[4/6] Ensuring Playwright browser dependencies..."
python -m playwright install chromium || true

echo "[5/6] Starting Ollama and downloading local model (gemma4:e2b, ~4.6 GB)..."
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  nohup ollama serve >/tmp/boldest_ollama.log 2>&1 &
  for _ in {1..15}; do
    if curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi
ollama pull gemma4:e2b

echo "[6/6] Optional: Ngrok authentication"
echo "If you haven't connected ngrok yet, get your free authtoken at:"
echo "  https://dashboard.ngrok.com/get-started/your-authtoken"
read -r -p "Paste your ngrok authtoken (or press Enter to skip): " NGROK_TOKEN
if [ -n "$NGROK_TOKEN" ]; then
  ngrok config add-authtoken "$NGROK_TOKEN"
fi

echo "===================================================="
echo " SETUP COMPLETE!"
echo " Next steps:"
echo " 1. Double-click mac/login_linkedin.command to sign into LinkedIn."
echo " 2. Double-click mac/start_omnichannel.command to run the local server."
echo "===================================================="
read -r -p "Press Enter to close..."
