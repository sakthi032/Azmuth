# scripts\LaunchBrowser.ps1
# Launch any installed Chromium browser with the DevTools Protocol.
#
# Usage:
#   powershell -File scripts\LaunchBrowser.ps1 chrome
#   powershell -File scripts\LaunchBrowser.ps1 edge
#   powershell -File scripts\LaunchBrowser.ps1 brave
#   powershell -File scripts\LaunchBrowser.ps1 opera
#   powershell -File scripts\LaunchBrowser.ps1 vivaldi

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("chrome","edge","brave","opera","vivaldi","chromium")]
    [string]$Browser
)

$ErrorActionPreference = "SilentlyContinue"

$candidates = @{
    chrome   = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    edge     = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    )
    brave    = @(
        "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe"
    )
    opera    = @(
        "$env:LOCALAPPDATA\Programs\Opera\opera.exe",
        "C:\Program Files\Opera\opera.exe"
    )
    vivaldi  = @(
        "$env:LOCALAPPDATA\Vivaldi\Application\vivaldi.exe",
        "C:\Program Files\Vivaldi\Application\vivaldi.exe"
    )
    chromium = @(
        "$env:LOCALAPPDATA\Chromium\Application\chrome.exe"
    )
}

$ports = @{
    chrome=9222; edge=9223; brave=9224; opera=9225; vivaldi=9226; chromium=9227
}

$exe = $null
foreach ($path in $candidates[$Browser]) {
    if (Test-Path $path) { $exe = $path; break }
}
if (-not $exe) {
    Write-Host "$Browser is not installed." -ForegroundColor Red
    exit 1
}

$port = $ports[$Browser]
$profileDir = "$env:LOCALAPPDATA\Azmuth\browsers\$Browser"
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

# Kill running instances of THIS browser only.
taskkill /IM (Split-Path $exe -Leaf) /F 2>$null | Out-Null
Start-Sleep -Milliseconds 1000

Start-Process -FilePath $exe -ArgumentList @(
    "--user-data-dir=$profileDir",
    "--profile-directory=Default",
    "--remote-debugging-port=$port",
    "--no-first-run",
    "--no-default-browser-check"
)

Start-Sleep -Seconds 3
Write-Host "$Browser launched with debug port $port." -ForegroundColor Green
Write-Host "First run: sign in to Gmail once in this window." -ForegroundColor Yellow