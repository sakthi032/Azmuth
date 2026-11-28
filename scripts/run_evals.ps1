# Run the Azmuth evaluation suite.
#
# Usage:
#   .\scripts\run_evals.ps1            # full offline run
#   .\scripts\run_evals.ps1 -Fast      # 15 cases only
#   .\scripts\run_evals.ps1 -Category chat
#   .\scripts\run_evals.ps1 -SaveBaseline

param(
    [switch]$Fast,
    [string]$Category = "",
    [switch]$SaveBaseline,
    [double]$Threshold = 0.80
)

$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $args = @("-m", "app.evals", "--threshold", "$Threshold")
    if ($Fast) { $args += "--fast" }
    if ($Category) { $args += @("--category", $Category) }
    if ($SaveBaseline) { $args += "--save-baseline" }

    Write-Host "Running: py $($args -join ' ')"
    & py @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}