@echo off
REM Launch Chrome with the DevTools Protocol enabled so Azmuth can
REM list and close tabs. Uses a copy of your profile so logins survive.

set PROFILE=%LOCALAPPDATA%\Azmuth\chrome-cdp-profile
set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe

if not exist "%CHROME%" set CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe

REM First run: copy the profile once.
if not exist "%PROFILE%" (
    echo Copying Chrome profile to %PROFILE% (one-time setup)...
    robocopy "%LOCALAPPDATA%\Google\Chrome\User Data" "%PROFILE%" /E /XD "Cache" "Code Cache" "GPUCache" "Service Worker" "ShaderCache" /NFL /NDL /NJH /NJS /NP >nul
    echo Done.
)

REM Make sure nothing else is running on 9222.
taskkill /IM chrome.exe /F >nul 2>&1
timeout /t 1 /nobreak >nul

start "" "%CHROME%" ^
    --user-data-dir="%PROFILE%" ^
    --profile-directory="Default" ^
    --remote-debugging-port=9222

echo.
echo Chrome launched with debug port 9222.
echo Azmuth can now list and close tabs.