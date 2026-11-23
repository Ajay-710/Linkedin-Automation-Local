#!/bin/bash
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "===================================================="
echo " BOLDEST LinkedIn One-Time Persistent Browser Login"
echo "===================================================="
echo "1. A Google Chrome window will open."
echo "2. Sign into YOUR LinkedIn account."
echo "3. Once your LinkedIn Feed loads, close the Chrome window."
echo "===================================================="

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/linkedin-local-service/server.py" --login
else
  python3 "$REPO_ROOT/linkedin-local-service/server.py" --login
fi

read -r -p "Done! Press Enter to close..."
