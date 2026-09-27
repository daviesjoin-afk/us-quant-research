# Stage 4-C IBKR Live adapter exact-one mutation harness.
# Each mutation changes one safety decision and must be killed by its named test.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"

function Resolve-Python {
    foreach ($candidate in @(
        $env:USQUANT_PYTHON,
        (Join-Path $projectRoot ".venv314\Scripts\python.exe"),
        (Join-Path $projectRoot ".venv313\Scripts\python.exe"),
        (Join-Path $projectRoot ".venv\Scripts\python.exe")
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if (-not $onPath) { exit 2 }
    return $onPath.Source
}

$py = Resolve-Python
$adapter = Join-Path $projectRoot "src\us_quant\trading\adapters\ibkr\live_execution.py"
$tests = Join-Path $projectRoot "tests\test_ibkr_live_execution_adapter.py"
function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$mutations = @(
    @{ name='read-only IBKR API is accepted for orders'; find='if config\.api_read_only:'; repl='if False:'; select='test_only_the_non_readonly_loopback_live_profile_is_accepted' },
    @{ name='M2 multiple managed accounts are accepted'; find='if len\(accounts\) != 1:'; repl='if len(accounts) < 1:'; select='test_managed_account_must_be_one_exact_bound_live_account' },
    @{ name='M3 unknown symbols are accepted'; find='if intent\.execution_symbol not in self\.allowed_symbols:'; repl='if False:'; select='test_unapproved_symbol_strategy_fractional_or_oversell_is_refused' },
    @{ name='M4 unbound strategy versions are accepted'; find='if intent\.strategy_version_id not in self\.allowed_strategy_version_ids:'; repl='if False:'; select='test_unapproved_symbol_strategy_fractional_or_oversell_is_refused' },
    @{ name='M5 SELL can exceed confirmed long position'; find='if intent\.quantity > owned - reserved:'; repl='if False:'; select='test_unapproved_symbol_strategy_fractional_or_oversell_is_refused' },
    @{ name='M6 orders are no longer limit orders'; find='order\.orderType = "LMT"'; repl='order.orderType = "MKT"'; select='test_reserve_only_allocates_id_and_submit_builds_one_live_lmt_order' },
    @{ name='M7 cancellation can target an untracked order'; find='if broker_order_id is None or intent is None:'; repl='if False:'; select='test_sell_reservations_prevent_overselling_and_cancel_only_tracked_orders' },
    @{ name='M8 uncertain submit does not halt'; find='self\._halt_locked\(f"Live submission uncertain: \{error\}"\)'; repl='pass'; select='test_uncertain_submit_halts_adapter_and_never_retries' },
    @{ name='M9 execution account mismatch is accepted'; find='if execution_account != self\._account:'; repl='if False:'; select='test_execution_fill_mismatch_halts_without_emitting_fill' },
    @{ name='M10 overfill beyond reserved quantity is accepted'; find='if cumulative_quantity > Decimal\(intent\.quantity\):'; repl='if False:'; select='test_execution_fill_mismatch_halts_without_emitting_fill' },
    @{ name='M11 already submitted reservation is submitted again'; find='if reservation\.broker_order_id in self\._submitted_orders:'; repl='if False:'; select='test_reserve_only_allocates_id_and_submit_builds_one_live_lmt_order' },
    @{ name='M12 conflicting intent reuses an existing broker id'; find='if self\._intent_by_order\[existing\] != intent:'; repl='if False:'; select='test_reusing_an_order_id_for_different_intent_is_refused' },
    @{ name='non-stock or non-USD positions are accepted'; find='if security_type != "STK" or currency != "USD":'; repl='if False:'; select='test_non_usd_stock_startup_position_fails_closed' },
    @{ name='invalid filled or remaining values are trusted'; find='if quantities_valid:'; repl='if True:'; select='test_invalid_order_status_quantities_halt_and_publish_unknown' },
    @{ name='position stream overwrites locally reconciled fill'; find='if self\._position_snapshot_complete:'; repl='if False:'; select='test_broker_order_status_and_fill_callbacks_are_normalized_and_deduplicated' },
    @{ name='HALT still permits broker cancellation'; find='or self\._halted'; repl='or False'; select='test_uncertain_submit_halt_blocks_another_broker_cancel' },
    @{ name='global gateway errors do not halt'; find='elif code not in \{201, 202\}:'; repl='elif False:'; select='test_post_handshake_gateway_error_halts_and_cancel_is_blocked' },
    @{ name='timezone-less IBKR execution time remains naive'; find='            return parsed\.replace\(tzinfo=timezone\.utc\)'; repl='            return parsed'; select='test_broker_order_status_and_fill_callbacks_are_normalized_and_deduplicated' }
)

$results = @()
foreach ($mutation in $mutations) {
    $original = Get-Text $adapter
    $regex = [regex]::new($mutation.find)
    $matches = $regex.Matches($original)
    if ($matches.Count -ne 1) {
        $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=$false; Detail="expected 1 anchor, found $($matches.Count)" }
        continue
    }
    $backup = "$adapter.stage4c-mutation-backup"
    Copy-Item -LiteralPath $adapter -Destination $backup -Force
    try {
        Set-Text $adapter ($regex.Replace($original, $mutation.repl, 1))
        $output = & $py -m pytest $tests -q -k $mutation.select 2>&1
        $exitCode = $LASTEXITCODE
        $countLine = $output | Select-String -Pattern '\d+ failed' | Select-Object -First 1
        $assertionFailure = $output | Select-String -Pattern 'AssertionError|assert .* ==|assert .* is|DID NOT RAISE' -Quiet
        $caught = ($exitCode -eq 1 -and $countLine -and $assertionFailure)
        $detail = if ($caught) { $countLine.Line.Trim() } else { "exit=$exitCode; $($output -join ' ')" }
    }
    catch {
        $caught = $false
        $detail = $_.Exception.Message
    }
    finally {
        Copy-Item -LiteralPath $backup -Destination $adapter -Force
        Remove-Item -LiteralPath $backup -Force
    }
    $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=[bool]$caught; Detail=$detail }
    Write-Host ("{0,-56} RED={1}  {2}" -f $mutation.name, $caught, $detail)
}

$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED / HARNESS-ERROR: $($row.Mutation) -> $($row.Detail)" }
if ($uncaught.Count -gt 0) { exit 1 }
exit 0
