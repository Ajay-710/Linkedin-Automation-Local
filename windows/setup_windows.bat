@echo off
setlocal
cd /d "%~dp0.."

echo ====================================================
echo  BOLDEST Omnichannel Automation - Windows Setup
echo ====================================================

where python >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Installing Python 3.12 via winget...
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
    )
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3 was not found in PATH. Install Python 3.10+ from https://www.python.org/downloads/ and check "Add python.exe to PATH", then re-run this script.
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

echo [3/4] Installing/Checking Ollama and pulling gemma4:e2b (~4.6 GB)...
where ollama >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Installing Ollama via winget...
        winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
        set "PATH=%LocalAppData%\Programs\Ollama;%PATH%"
    )
)

where ollama >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Could not auto-install Ollama. Download and install it from https://ollama.com/download/windows then run: ollama pull gemma4:e2b
) else (
    start "" "ollama.exe" serve
    timeout /t 4 /nobreak >nul
    ollama pull gemma4:e2b
)

echo [4/4] Installing/Checking ngrok...
where ngrok >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Installing ngrok via winget...
        winget install -e --id Ngrok.Ngrok --accept-package-agreements --accept-source-agreements
    )
)

where ngrok >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Could not auto-install ngrok. Install from https://ngrok.com/download and run: ngrok config add-authtoken YOUR_TOKEN
) else (
    set /p NGROK_TOKEN="Paste your ngrok authtoken (or press Enter to skip): "
    if not "%NGROK_TOKEN%"=="" ngrok config add-authtoken %NGROK_TOKEN%
)

echo ====================================================
echo  SETUP COMPLETE!
echo  Next steps:
echo  1. Double-click windows\login_linkedin.bat to sign into LinkedIn.
echo  2. Double-click windows\start_omnichannel.bat to run the stack.
echo ====================================================
pause
