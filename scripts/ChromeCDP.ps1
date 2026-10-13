# scripts\ChromeCDP.ps1
# Launch Chrome with the DevTools Protocol enabled so Azmuth can
# control tabs. Uses a dedicated profile directory so Chrome 136+
# actually enables the debug port.

$ErrorActionPreference = "SilentlyContinue"

$profileDir = "$env:LOCALAPPDATA\Azmuth\chrome-azmuth-profile"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) {
    $chrome = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
}
if (-not (Test-Path $chrome)) {
    Write-Host "chrome.exe not found." -ForegroundColor Red
    Start-Sleep -Seconds 5
    exit 1
}

New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

# Kill any running Chrome so the debug port can actually bind.
taskkill /IM chrome.exe /F 2>$null | Out-Null
Start-Sleep -Milliseconds 1000

# Launch: default profile name inside our own user-data-dir.
Start-Process -FilePath $chrome -ArgumentList @(
    "--user-data-dir=$link",
    "--profile-directory=Default",
    "--remote-debugging-port=9222",
    "--remote-allow-origins=*",
    "--no-first-run",
    "--no-default-browser-check"
)
Start-Sleep -Seconds 3
Write-Host "Chrome started with debug port 9222." -ForegroundColor Green
Write-Host "Profile: $profileDir\Default" -ForegroundColor Green
Write-Host ""
Write-Host "First launch only: sign in to Gmail (sakthi007032@gmail.com)" -ForegroundColor Yellow
Write-Host "in this Chrome window. After that you won't have to log in again." -ForegroundColor Yellow