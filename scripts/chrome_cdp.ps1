# Launch Chrome with the debug port, using a junction to the real profile.

$ErrorActionPreference = "SilentlyContinue"

$link = "$env:LOCALAPPDATA\Azmuth\chrome-cdp-profile"
$real = "$env:LOCALAPPDATA\Google\Chrome\User Data"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"

if (-not (Test-Path $chrome)) {
    $chrome = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
}
if (-not (Test-Path $chrome)) {
    Write-Host "chrome.exe not found." -ForegroundColor Red
    Start-Sleep -Seconds 5
    exit 1
}

# Recreate the junction if missing or broken.
$parent = Split-Path $link -Parent
New-Item -ItemType Directory -Force -Path $parent | Out-Null

$valid = $false
try {
    $item = Get-Item $link -ErrorAction Stop
    if ($item.LinkType -eq "Junction" -and $item.Target -eq $real) { $valid = $true }
} catch { $valid = $false }

if (-not $valid) {
    Remove-Item $link -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Junction -Path $link -Target $real | Out-Null
    Write-Host "Junction created." -ForegroundColor Green
}

# Kill any running Chrome, then launch through the junction.
taskkill /IM chrome.exe /F 2>$null | Out-Null
Start-Sleep -Milliseconds 800

Start-Process -FilePath $chrome -ArgumentList @(
    "--user-data-dir=$link",
    "--profile-directory=Default",
    "--remote-debugging-port=9222",
    "--no-first-run",
    "--no-default-browser-check"
)

# Wait for the port.
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:9222/json/version" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
}

if ($ok) {
    Write-Host "Chrome ready - debug port 9222 is live." -ForegroundColor Green
} else {
    Write-Host "Chrome launched but port 9222 did not respond." -ForegroundColor Yellow
}

Start-Sleep -Seconds 2