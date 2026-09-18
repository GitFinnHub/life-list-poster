@echo off
title Life List Poster
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Couldn't find .venv - has this project moved or been reinstalled?
    echo See README.md for setup instructions.
    pause
    exit /b 1
)

if not exist ".env" (
    echo Missing .env file - see .env.example for what's needed.
    pause
    exit /b 1
)

echo Starting the Life List Poster website...
echo.
echo This window shows what the site is doing - you can ignore it, just
echo don't close it while you're using the site. Your browser will open
echo automatically in about 15 seconds (the first startup is slow).
echo.
echo When you're done, close this window to stop the site.
echo.

start "" /b cmd /c "timeout /t 15 /nobreak >nul && start http://127.0.0.1:8321/"

.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8321

echo.
echo The site has stopped.
pause
