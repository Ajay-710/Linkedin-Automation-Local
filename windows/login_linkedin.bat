@echo off
cd /d "%~dp0.."
echo ====================================================
echo  BOLDEST LinkedIn One-Time Persistent Browser Login
echo ====================================================
echo 1. Sign into YOUR LinkedIn account in the Chrome window that opens.
echo 2. Once you see your LinkedIn Feed, simply CLOSE the Chrome window.
echo ====================================================
if exist "%CD%\.venv\Scripts\python.exe" (
    "%CD%\.venv\Scripts\python.exe" "%CD%\linkedin-local-service\server.py" --login
) else (
    python "%CD%\linkedin-local-service\server.py" --login
)
pause
