# Stage 4-D Live canary guard and composition exact-one mutation harness.
# Each mutation changes one authority check and must be killed by its named test.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$py = $env:USQUANT_PYTHON
if (-not $py) { $py = Join-Path $projectRoot ".venv314\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$guard = Join-Path $projectRoot "src\us_quant\trading\application\live_canary_execution.py"
$composition = Join-Path $projectRoot "src\us_quant\trading\composition\execution.py"
$behaviour = Join-Path $projectRoot "tests\test_live_canary_execution.py"
$deployment = Join-Path $projectRoot "tests\test_execution_environment.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$mutations = @(
    @{ file=$composition; name='M1 Live feature flag is bypassed'; find='if not live_trading_enabled:'; repl='if False:'; tests=$deployment; select='test_live_feature_flag_remains_required_when_all_canary_facts_are_present' },
    @{ file=$composition; name='M2 non-Live deployment can construct the Live candidate'; find='if environment is not Environment\.LIVE:'; repl='if False:'; tests=$deployment; select='test_backtest_never_selects_live_even_with_authorization_context' },
    @{ file=$guard; name='M3 stale startup and account truth are accepted'; find='if not self\._proof_facts_are_fresh\(proof, truth, now\):'; repl='if False:'; tests=$behaviour; select='test_stale_or_mismatched_startup_proof_cannot_authorize_live_order' },
    @{ file=$guard; name='M4 an older authorization digest is accepted'; find='if proof\.authorization_fingerprint != authorization\.authorization_fingerprint:'; repl='if False:'; tests=$behaviour; select='test_old_startup_proof_cannot_authorize_a_replaced_limit_set' },
    @{ file=$guard; name='M5 an old session arm can be reused'; find='if \(\s+not state\.session_armed\s+or state\.session_arm_id is None\s+or proof\.session_arm_id != state\.session_arm_id\s+\):'; repl='if False:'; tests=$behaviour; select='test_guard_rechecks_ephemeral_session_arm_before_submit' },
    @{ file=$guard; name='M6 a persistent kill latch does not block BUY'; find='if state\.kill_latch\.is_latched:'; repl='if False:'; tests=$behaviour; select='test_kill_latch_blocks_new_exposure_even_if_an_old_arm_bit_survives' },
    @{ file=$guard; name='M7 BUY notional exceeds the operator cap'; find='if notional > limits\.max_order_notional:'; repl='if False:'; tests=$behaviour; select='test_guard_fails_closed_at_each_canary_exposure_limit' },
    @{ file=$guard; name='M8 capital limit is ignored'; find='if \(\s*truth\.gross_exposure\s*\+\s*truth\.open_buy_notional\s*\+\s*reserved_notional\s*\+\s*notional\s*>\s*exposure_cap\s*\):'; repl='if False:'; tests=$behaviour; select='test_guard_fails_closed_at_each_canary_exposure_limit or test_external_open_buy_notional_counts_against_the_capital_limit' },
    @{ file=$guard; name='M9 daily loss limit is ignored'; find='if daily_loss >= limits\.max_daily_loss:'; repl='if False:'; tests=$behaviour; select='test_guard_fails_closed_at_each_canary_exposure_limit' },
    @{ file=$guard; name='M10 position count limit is ignored'; find='if projected_positions > limits\.max_positions:'; repl='if False:'; tests=$behaviour; select='test_guard_fails_closed_at_each_canary_exposure_limit or test_broker_visible_open_buy_counts_toward_position_cap' },
    @{ file=$guard; name='M11 open order limit is ignored'; find='if truth\.open_order_count \+ active_count >= limits\.max_open_orders:'; repl='if False:'; tests=$behaviour; select='test_guard_fails_closed_at_each_canary_exposure_limit' },
    @{ file=$guard; name='M12 symbol allowlist is ignored'; find='not in \{symbol\.strip\(\)\.upper\(\) for symbol in limits\.allowed_symbols\}'; repl='in set()'; tests=$behaviour; select='test_guard_fails_closed_at_each_canary_exposure_limit' },
    @{ file=$guard; name='M13 strategy allowlists are ignored'; find='or intent\.strategy_version_id not in authorization\.approved_strategy_version_ids\s+or intent\.strategy_version_id not in limits\.allowed_strategy_versions'; repl='or False'; tests=$behaviour; select='test_guard_fails_closed_at_each_canary_exposure_limit' },
    @{ file=$guard; name='M14 SELL may exceed owned unreserved shares'; find='if intent\.side is Side\.SELL and not reducing:'; repl='if False:'; tests=$behaviour; select='test_reserved_sell_capacity_prevents_two_concurrent_exits_from_overselling or test_broker_visible_sell_reservation_is_subtracted_from_owned_shares' },
    @{ file=$guard; name='M15 the kill latch blocks a risk-reducing exit'; find='emergency_exit = reducing and state\.kill_latch\.is_latched'; repl='emergency_exit = False'; tests=$behaviour; select='test_kill_allows_an_owned_share_risk_reducing_exit' },
    @{ file=$guard; name='M16 a cleared kill restores an old session arm'; find='if \(\s*state\.session_arm_revision != durable_state\.revision\s*or proof\.session_arm_revision != durable_state\.revision\s*\):'; repl='if False:'; tests=$behaviour; select='test_clear_kill_does_not_restore_arm_from_an_older_safety_revision' }
)

$results = @()
foreach ($mutation in $mutations) {
    $original = Get-Text $mutation.file
    $regex = [regex]::new($mutation.find)
    $matches = $regex.Matches($original)
    if ($matches.Count -ne 1) {
        $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=$false; Detail="expected 1 anchor, found $($matches.Count)" }
        continue
    }
    $backup = "$($mutation.file).stage4d-mutation-backup"
    Copy-Item -LiteralPath $mutation.file -Destination $backup -Force
    try {
        Set-Text $mutation.file ($regex.Replace($original, $mutation.repl, 1))
        $output = & $py -m pytest $mutation.tests -q -k $mutation.select 2>&1
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
        Copy-Item -LiteralPath $backup -Destination $mutation.file -Force
        Remove-Item -LiteralPath $backup -Force
    }
    $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=[bool]$caught; Detail=$detail }
    Write-Host ("{0,-54} RED={1}  {2}" -f $mutation.name, $caught, $detail)
}

$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED / HARNESS-ERROR: $($row.Mutation) -> $($row.Detail)" }
if ($uncaught.Count -gt 0) { exit 1 }
exit 0
