# Stage 4-E Live recovery and kill/restart exact-one mutation harness.
# Each mutation bypasses one recovery boundary and must fail its behavioral proof.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$py = $env:USQUANT_PYTHON
if (-not $py) { $py = Join-Path $projectRoot ".venv314\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$guard = Join-Path $projectRoot "src\us_quant\trading\application\live_canary_execution.py"
$startup = Join-Path $projectRoot "src\us_quant\trading\domain\live_startup.py"
$recovery = Join-Path $projectRoot "src\us_quant\trading\application\live_recovery.py"
$safetyRepository = Join-Path $projectRoot "src\us_quant\trading\adapters\sqlite\live_safety_repository.py"
$composition = Join-Path $projectRoot "src\us_quant\trading\composition\execution.py"
$safety = Join-Path $projectRoot "src\us_quant\trading\domain\live_safety.py"
$behaviour = Join-Path $projectRoot "tests\test_live_canary_execution.py"
$recoveryTests = Join-Path $projectRoot "tests\test_live_recovery.py"
$startupTests = Join-Path $projectRoot "tests\test_live_startup.py"
$deploymentTests = Join-Path $projectRoot "tests\test_execution_environment.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$mutations = @(
    @{ file=$guard; name='E1 disconnect can reconnect without a recovery barrier'; find='lease\.require_reconciliation\(\s+at=self\._now\(\),\s+reason="Live broker disconnected",\s+\)'; repl='pass'; tests=$behaviour; select='test_disconnect_persists_recovery_barrier_and_reconnect_does_not_clear_it' },
    @{ file=$guard; name='E2 uncertain submission skips durable recovery'; find='broker_order_id=reservation\.broker_order_id,'; repl='broker_order_id=None,'; tests=$behaviour; select='test_uncertain_submission_persists_recovery_and_is_never_retried' },
    @{ file=$guard; name='E3 guard ignores the durable recovery barrier'; find='if durable_state\.recovery_latch\.is_required:'; repl='if False:'; tests=$behaviour; select='test_disconnect_persists_recovery_barrier_and_reconnect_does_not_clear_it' },
    @{ file=$startup; name='E4 startup proof accepts a latched recovery barrier'; find='if authorization_state\.recovery_latch\.is_required:'; repl='if False:'; tests=$startupTests; select='test_startup_proof_is_blocked_while_recovery_barrier_is_latched' },
    @{ file=$recovery; name='E5 dirty or account-mismatched recovery evidence is accepted'; find='if not isinstance\(evidence, LiveRecoveryEvidence\) or not evidence\.is_clean:'; repl='if False:'; tests=$recoveryTests; select='test_recovery_refuses_incomplete_or_mismatched_broker_evidence' },
    @{ file=$recovery; name='E6 stale reconciliation evidence can clear recovery'; find='if not evidence\.observed_at <= now < evidence\.observed_at \+ LIVE_STARTUP_PROOF_TTL:'; repl='if False:'; tests=$recoveryTests; select='test_recovery_requires_fresh_evidence_and_explicit_operator_confirmation' },
    @{ file=$recovery; name='E7 recovery can clear without explicit operator confirmation'; find='if type\(operator_confirmed\) is not bool or not operator_confirmed:'; repl='if False:'; tests=$recoveryTests; select='test_recovery_requires_fresh_evidence_and_explicit_operator_confirmation' },
    @{ file=$recovery; name='E8 uncertain broker order id is not matched to reconciliation'; find='and current\.recovery_latch\.broker_order_id\s+not in evidence\.reconciled_broker_order_ids'; repl='and False'; tests=$recoveryTests; select='test_uncertain_order_must_be_present_in_reconciliation_evidence' },
    @{ file=$composition; name='E9 Live process starts without a fresh reconciliation barrier'; find='durable_state = LiveCanaryRecovery\(live_safety_repository\)\.require_reconciliation\(\s+reason="Live process start requires fresh broker reconciliation"\s+\)'; repl='durable_state = durable_state'; tests=$deploymentTests; select='test_live_composition_latches_fresh_reconciliation_on_process_start' }
    @{ file=$safetyRepository; name='E10 a later unsafe event does not refresh an existing barrier'; find='recovery = self\._record\.recovery_latch\.require\(\s+at=at,\s+reason=reason,\s+broker_order_id=broker_order_id,\s+\)'; repl='recovery = self._record.recovery_latch'; tests=$recoveryTests; select='test_later_unsafe_event_refreshes_barrier_and_invalidates_earlier_evidence' }
    @{ file=$safety; name='E11 an inactive recovery latch accepts a broker order id'; find='if not self\.is_required and self\.broker_order_id is not None:'; repl='if False:'; tests=$recoveryTests; select='test_recovery_order_id_cannot_exist_without_active_barrier' }
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
    $backup = "$($mutation.file).stage4e-mutation-backup"
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
    Write-Host ("{0,-68} RED={1}  {2}" -f $mutation.name, $caught, $detail)
}

$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED / HARNESS-ERROR: $($row.Mutation) -> $($row.Detail)" }
if ($uncaught.Count -gt 0) { exit 1 }
exit 0
