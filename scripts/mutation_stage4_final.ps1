# Final Stage 4 mutation gate: all Stage 4 scopes, Stage 3 Final, and FAC.
# Each child harness runs in its own PowerShell process so its `exit` cannot
# skip the remaining suites. Any survivor or harness error fails this runner.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$powerShell = Get-Command pwsh.exe -ErrorAction SilentlyContinue
if (-not $powerShell) {
    $powerShell = Get-Command powershell.exe -ErrorAction Stop
}

$harnesses = @(
    "mutation_live_safety_a.ps1",
    "mutation_live_startup_b.ps1",
    "mutation_live_execution_c.ps1",
    "mutation_live_canary_d.ps1",
    "mutation_live_canary_e.ps1",
    "mutation_live_operator_f.ps1",
    "mutation_live_ready_execution_final.ps1",
    "mutation_final_architecture_closure.ps1"
)

Write-Host "Stage 4 final mutation gate: $($harnesses.Count) independent harnesses"
foreach ($name in $harnesses) {
    $path = Join-Path $PSScriptRoot $name
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host "MISSING HARNESS: $path" -ForegroundColor Red
        exit 2
    }
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    & $powerShell.Source -NoProfile -ExecutionPolicy Bypass -File $path
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-Host "FAILED: $name (exit $exitCode)" -ForegroundColor Red
        exit $exitCode
    }
}

Write-Host "`nAll Stage 4, Stage 3 Final, and FAC mutation harnesses passed." -ForegroundColor Green
exit 0
