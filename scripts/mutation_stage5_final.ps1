# Stage 5 Final aggregate mutation gate.
# Run each independent stage harness in order and fail closed on any gap.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$powerShell = Get-Command pwsh.exe -ErrorAction SilentlyContinue
if (-not $powerShell) {
    $powerShell = Get-Command powershell.exe -ErrorAction Stop
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $projectRoot ".venv314\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}
$env:US_QUANT_PYTHON = $python
$env:USQUANT_PYTHON = $python
$env:PATH = "$(Split-Path -Parent $python);$env:PATH"
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"

$harnesses = @(
    "mutation_stage5_final_composition_guard.ps1",
    "mutation_portfolio_runtime_a.ps1",
    "mutation_portfolio_runtime_b.ps1",
    "mutation_portfolio_runtime_c.ps1",
    "mutation_portfolio_reconciliation_d.ps1",
    "mutation_portfolio_operations_e.ps1",
    "mutation_stage4_final.ps1"
)

$summaryPattern = [regex]::new(
    'mutations\s*[:=]\s*(\d+).*?red\s*[:=]\s*(\d+).*?(?:not caught|survivor_or_error)\s*[:=]\s*(\d+)',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
)

foreach ($name in $harnesses) {
    $path = Join-Path $PSScriptRoot $name
    Write-Host "`n=== child harness: $name ===" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host "mutation count=0; red count=0; survivors=0; harness errors=1 (missing child)" -ForegroundColor Red
        exit 1
    }

    $output = & $powerShell.Source -NoProfile -ExecutionPolicy Bypass -File $path 2>&1
    $exitCode = $LASTEXITCODE
    $summaries = @()
    foreach ($line in $output) {
        $match = $summaryPattern.Match([string]$line)
        if ($match.Success) {
            $summaries += [pscustomobject]@{
                Mutations = [int]$match.Groups[1].Value
                Red = [int]$match.Groups[2].Value
                Survivors = [int]$match.Groups[3].Value
                Text = ([string]$line).Trim()
            }
        }
    }

    foreach ($summary in $summaries) {
        Write-Host ("mutation count={0}; red count={1}; survivors={2}; harness errors=0" -f `
            $summary.Mutations, $summary.Red, $summary.Survivors)
        Write-Host "  $($summary.Text)"
    }
    if ($exitCode -ne 0) {
        $output | ForEach-Object { Write-Host $_ }
        Write-Host "child harness failed with exit $exitCode; harness errors=1" -ForegroundColor Red
        exit 1
    }
    if ($summaries.Count -eq 0) {
        $output | ForEach-Object { Write-Host $_ }
        Write-Host "child produced no mutation summary; harness errors=1" -ForegroundColor Red
        exit 1
    }
    $invalid = @($summaries | Where-Object {
        $_.Mutations -le 0 -or $_.Red -ne $_.Mutations -or $_.Survivors -ne 0
    })
    if ($invalid.Count -gt 0) {
        Write-Host "survivors or mutation harness errors detected; fail closed" -ForegroundColor Red
        exit 1
    }
    Write-Host "child harness status=PASS" -ForegroundColor Green
}

Write-Host "`nStage 5 aggregate mutation gate: PASS (all child mutations RED; zero survivors or harness errors)." -ForegroundColor Green
exit 0
