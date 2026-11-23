@echo off
setlocal
cd /d "%~dp0.."

echo ====================================================
echo  BOLDEST Omnichannel Automation - Windows Setup
echo ====================================================

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3 was not found in PATH. Install Python 3.10+ from https://www.python.org/downloads/ and check "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [1/4] Creating Python virtual environment and installing dependencies...
python -m venv "%CD%\.venv"
call "%CD%\.venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r "%CD%\linkedin-local-service\requirements.txt"

echo [2/4] Ensuring Playwright browser dependencies...
python -m playwright install chromium

echo [3/4] Checking Ollama and pulling local model (gemma4:e2b)...
where ollama >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Ollama not found. Install Ollama from https://ollama.com/download/windows then run: ollama pull gemma4:e2b
) else (
    start "" "ollama.exe" serve
    timeout /t 3 /nobreak >nul
    ollama pull gemma4:e2b
)

echo [4/4] Checking ngrok...
where ngrok >nul 2>&1
if errorlevel 1 (
    echo [WARNING] ngrok not found. Install from https://ngrok.com/download and run: ngrok config add-authtoken YOUR_TOKEN
)

echo ====================================================
echo  SETUP COMPLETE!
echo  Next steps:
echo  1. Double-click windows\login_linkedin.bat to sign into LinkedIn.
echo  2. Double-click windows\start_omnichannel.bat to run the stack.
echo ====================================================
pause
