# Stage 5-D portfolio reconciliation mutation gate.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$py = Join-Path $projectRoot ".venv314\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$domain = Join-Path $projectRoot "src\us_quant\trading\domain\portfolio_reconciliation.py"
$application = Join-Path $projectRoot "src\us_quant\trading\application\portfolio_reconciliation.py"
$tests = Join-Path $projectRoot "tests\test_portfolio_reconciliation.py"
$mutations = @(
    @{ name='M1 position mismatch ignored'; file=$domain; find='elif broker_quantity != Decimal\(attributed_quantity\):'; repl='elif False:'; select='matching_broker_position_passes_and_mismatch_blocks' },
    @{ name='M2 unknown broker position accepted'; file=$domain; find='if symbol in broker_quantities and symbol not in strategy_quantities and broker_quantity != 0:'; repl='if False:'; select='unknown_external_position_blocks_without_assigning_strategy' },
    @{ name='M3 partial fill attribution depends on callback order'; file=$domain; find='for _occurred_at, _execution_id, order_id, fill in sorted\(replay_batches\):'; repl='for _occurred_at, _execution_id, order_id, fill in replay_batches:'; select='partial_fill_largest_remainder_is_callback_order_independent' },
    @{ name='M4 SELL reduces wrong strategy'; file=$domain; find='key = \(strategy_id, fill\.symbol\.strip\(\)\.upper\(\)\)'; repl='key = ("wrong-strategy", fill.symbol.strip().upper())'; select='partial_sell_reduces_only_selling_strategy' },
    @{ name='M5 fees discarded'; file=$domain; find='accounting\[\(strategy_id, fill\.symbol\.strip\(\)\.upper\(\)\)\]\.fees \+= fee'; repl='pass'; select='fee_and_realized_pnl_are_deterministic' },
    @{ name='M6 restart trusts process memory'; file=$application; find='order_truth=self\._order_truth\.portfolio_order_truth\(\),'; repl='order_truth=type(self._order_truth.portfolio_order_truth())(orders=()),'; select='restart_rebuilds_positions_from_durable_portfolio_and_order_facts' },
    @{ name='M7 reconciliation blocker bypassed'; file=$domain; find='return not self\.blockers'; repl='return True'; select='matching_broker_position_passes_and_mismatch_blocks' },
    @{ name='M8 fills replay by random order id'; file=$domain; find='for _occurred_at, _execution_id, order_id, fill in sorted\(replay_batches\):'; repl='for _occurred_at, _execution_id, order_id, fill in sorted(replay_batches, key=lambda batch: batch[2]):'; select='fill_history_replays_globally_by_time_not_random_order_id' },
    @{ name='M9 wrong broker event link accepted'; file=$domain; find='if event is not None and event\.broker_order_id != order\.broker_order_id:'; repl='if False:'; select='callback_with_wrong_broker_order_id_is_unexplained' },
    @{ name='M10 wrong broker fill link accepted'; file=$domain; find='or fill\.broker_order_id != order\.broker_order_id'; repl='or False'; select='fill_with_wrong_broker_order_id_is_unexplained' },
    @{ name='M11 unknown broker-side open order is ignored'; file=$domain; find='if set\(broker_open_by_id\) != set\(local_open_by_broker_id\):'; repl='if False:'; select='unknown_broker_open_order_blocks_even_when_local_ledger_is_empty' },
    @{ name='M12 opposing contributor slippage follows physical order side'; file=$domain; find='contribution_sign = 1 if signed_quantity > 0 else -1'; repl='contribution_sign = 1 if fill.side.value == "buy" else -1'; select='opposing_contributor_slippage_uses_signed_strategy_direction' }
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
    $backup = "$($mutation.file).stage5d-backup"
    Copy-Item -LiteralPath $mutation.file -Destination $backup -Force
    try {
        Set-Text $mutation.file ($regex.Replace($original, $mutation.repl, 1))
        $syntax = & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $mutation.file 2>&1
        if ($LASTEXITCODE -ne 0) {
            $failures += "$($mutation.name): syntax error: $($syntax -join ' ')"
            Write-Host "RED=False $($mutation.name)"
            continue
        }
        $output = & $py -m pytest $tests -q -k $mutation.select 2>&1
        $exitCode = $LASTEXITCODE
        $caught = ($exitCode -eq 1) -and ($output | Select-String -Pattern 'AssertionError|DID NOT RAISE|PortfolioStoreUnreadable|PortfolioError|AttributeError|ValueError|KeyError' -Quiet)
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
