# Stage 5-C multi-strategy runtime mutation gate.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$py = Join-Path $projectRoot ".venv314\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$runtime = Join-Path $projectRoot "src\us_quant\trading\application\portfolio_runtime.py"
$translation = Join-Path $projectRoot "src\us_quant\trading\application\portfolio_translation.py"
$runtimeTests = Join-Path $projectRoot "tests\test_portfolio_runtime.py"
$architectureTests = Join-Path $projectRoot "tests\test_portfolio_architecture.py"
$mutations = @(
    @{ name='M1 portfolio runtime bypasses Risk'; file=$runtime; find='risk_decision = self\._risk_path\.evaluate\(decision, observed_at=observed_at\)'; repl='risk_decision = RiskDecision.approve(requested_quantity=decision.action.quantity)'; test=$runtimeTests; select='risk_reject_is_persisted_and_never_submitted' },
    @{ name='M2 zero-net decision reaches Risk'; file=$runtime; find='or decision\.action is None'; repl='or False'; test=$runtimeTests; select='zero_net_and_hold_never_reach_risk' },
    @{ name='M3 persisted decision executes twice'; file=$runtime; find='if previous is not None and previous\.risk_outcome is not None:'; repl='if False:'; test=$runtimeTests; select='uncertain_execution_and_restart_do_not_regenerate_action' },
    @{ name='M4 pending action does not reserve capital'; file=$runtime; find='and record\.order_id is None'; repl='and False'; test=$runtimeTests; select='unlinked_approved_buy_reserves_cash_for_the_next_cycle' },
    @{ name='M5 disabled allocation enters runtime'; file=$runtime; find='and allocation\.enabled'; repl='and True'; test=$runtimeTests; select='selected_version_with_disabled_allocation_fails_closed' },
    @{ name='M6 HOLD becomes executable intent'; file=$translation; find='if proposal\.action is TradeAction\.HOLD:'; repl='if False:'; test=$runtimeTests; select='zero_net_and_hold_never_reach_risk' },
    @{ name='M7 Risk outcome is not persisted before dispatch'; file=$runtime; find='record = self\._repository\.update_decision\(\s+replace\(\s+record,\s+risk_outcome=risk_outcome,\s+risk_decision=risk_decision,\s+\),\s+expected_revision=record\.revision,\s+\)'; repl='record = record'; test=$runtimeTests; select='uncertain_execution_and_restart_do_not_regenerate_action' },
    @{ name='M8 portfolio runtime constructs OrderIntent'; file=$runtime; find='class PortfolioRuntime:'; repl="if False:`n    OrderIntent()`n`n`nclass PortfolioRuntime:"; test=$architectureTests; select='portfolio_runtime_is_unique_and_cannot_construct_execution_authority' },
    @{ name='M9 mixed observation window is accepted'; file=$runtime; find='if proposal\.generated_at != observed_at or proposal\.generated_at > proposal_cutoff:'; repl='if False:'; test=$runtimeTests; select='mixed_observation_windows_are_rejected' },
    @{ name='M10 restart loses uncertain dispatch halt'; file=$runtime; find='halt=\(\s+previous\.dispatch_halt\s+if previous\.dispatch_outcome_recorded\s+else True\s+\),'; repl='halt=False,'; test=$runtimeTests; select='uncertain_execution_and_restart_do_not_regenerate_action' },
    @{ name='M11 order linkage omits execution attribution'; file=$runtime; find='execution_attribution = \([\s\S]*?\n {16}\)'; repl='execution_attribution = None'; test=$runtimeTests; select='uncertain_execution_and_restart_do_not_regenerate_action' }
)

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$failures = @()
foreach ($mutation in $mutations) {
    $original = Get-Text $mutation.file
    $regex = [regex]::new($mutation.find)
    $matches = $regex.Matches($original)
    if ($matches.Count -ne 1) {
        $failures += "$($mutation.name): expected one anchor, found $($matches.Count)"
        continue
    }
    $backup = "$($mutation.file).stage5c-backup"
    Copy-Item -LiteralPath $mutation.file -Destination $backup -Force
    try {
        Set-Text $mutation.file ($regex.Replace($original, $mutation.repl, 1))
        $syntax = & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $mutation.file 2>&1
        if ($LASTEXITCODE -ne 0) {
            $failures += "$($mutation.name): syntax error: $($syntax -join ' ')"
            Write-Host "RED=False $($mutation.name)"
            continue
        }
        $output = & $py -m pytest $mutation.test -q -k $mutation.select 2>&1
        $exitCode = $LASTEXITCODE
        $caught = ($exitCode -eq 1) -and ($output | Select-String -Pattern 'AssertionError|DID NOT RAISE|PortfolioStoreUnreadable|PortfolioError|AttributeError|ValueError' -Quiet)
        Write-Host ("RED={0} {1}" -f [bool]$caught, $mutation.name)
        if (-not $caught) { $failures += "$($mutation.name): exit=$exitCode; $($output -join ' ')" }
    }
    finally {
        Copy-Item -LiteralPath $backup -Destination $mutation.file -Force
        Remove-Item -LiteralPath $backup -Force
    }
}
Write-Host "mutations=$($mutations.Count) red=$($mutations.Count - $failures.Count) survivor_or_error=$($failures.Count)"
foreach ($failure in $failures) { Write-Host "SURVIVED / HARNESS-ERROR: $failure" }
if ($failures.Count -gt 0) { exit 1 }
exit 0
