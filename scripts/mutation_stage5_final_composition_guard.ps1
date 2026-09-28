# Mutation check for the Stage 5 production Paper composition boundary.
# Add an indirect local helper constructor to desktop.py and require the
# Final Architecture Closure guard to reject it. The source is restored even
# when pytest or the mutation harness fails.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopPath = Join-Path $projectRoot "src\us_quant\desktop.py"
$testPath = Join-Path $projectRoot "tests\test_stage5_final_architecture_closure.py"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $projectRoot ".venv314\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$original = [System.IO.File]::ReadAllText($desktopPath)
$marker = "_stage5_mutant_paper_helper"
if ($original.Contains($marker)) {
    Write-Host "mutation anchor already exists; harness error=1" -ForegroundColor Red
    exit 1
}

$mutant = @'

import us_quant.trading.runtime as runtime


def _stage5_mutant_paper_helper():
    return runtime.TradingRuntime()
'@
$pytestExit = -1
$output = @()
try {
    [System.IO.File]::WriteAllText(
        $desktopPath,
        $original + $mutant,
        (New-Object System.Text.UTF8Encoding($false))
    )
    $output = & $python -m pytest $testPath -k production_paper_delegates -q 2>&1
    $pytestExit = $LASTEXITCODE
}
finally {
    [System.IO.File]::WriteAllText(
        $desktopPath,
        $original,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

$combined = ($output | ForEach-Object { [string]$_ }) -join "`n"
$caught = $pytestExit -eq 1 -and
    $combined.Contains("test_production_paper_delegates_to_one_portfolio_composition_root") -and
    $combined -match "1 failed"
if ($caught) {
    $output | ForEach-Object { Write-Host $_ }
    Write-Host "mutations: 1 red: 1 not caught: 0" -ForegroundColor Green
    exit 0
}

$output | ForEach-Object { Write-Host $_ }
if ($pytestExit -eq 0) {
    Write-Host "mutations: 1 red: 0 not caught: 1 (survived)" -ForegroundColor Red
}
else {
    Write-Host "mutations: 1 red: 0 not caught: 0 (harness error)" -ForegroundColor Red
}
exit 1
