@echo off
cd /d "%~dp0.."
echo ====================================================
echo  Starting BOLDEST Local Omnichannel Engine Stack
echo  (Local Ollama + Local LinkedIn Playwright + Ngrok)
echo ====================================================

taskkill /F /IM ollama.exe 2>nul
taskkill /F /IM "ollama app.exe" 2>nul
taskkill /F /IM ngrok.exe 2>nul

start "" "ollama.exe" serve
timeout /t 3 /nobreak >nul

if exist "%CD%\.venv\Scripts\python.exe" (
    start "BOLDEST Local LinkedIn + Ollama Server (Port 3005)" cmd /k """%CD%\.venv\Scripts\python.exe"" ""%CD%\linkedin-local-service\server.py"""
) else (
    start "BOLDEST Local LinkedIn + Ollama Server (Port 3005)" cmd /k "python ""%CD%\linkedin-local-service\server.py"""
)
timeout /t 2 /nobreak >nul

start "Ngrok Unified Tunnel (Ollama + LinkedIn)" cmd /k "ngrok http 3005"

echo ====================================================
echo  All 3 Services Started:
echo  1. Local Ollama:           http://127.0.0.1:11434
echo  2. Local LinkedIn Server:  http://127.0.0.1:3005/linkedin/action
echo  3. Ngrok Tunnel:           Copy the https:// URL into Config sheet & n8n
echo ====================================================
pause
