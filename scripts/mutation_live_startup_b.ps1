# Stage 4-B startup proof exact-one mutation harness.
# Each single-point mutant must be killed by its named behavioral test.

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
$domain = Join-Path $projectRoot "src\us_quant\trading\domain\live_startup.py"
$testFile = Join-Path $projectRoot "tests\test_live_startup.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$mutations = @(
    @{ name='M1 disconnected broker is accepted'; find='if not broker_connected:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and BROKER_DISCONNECTED' },
    @{ name='M2 stale account truth is accepted'; find='elif not _fresh\(account_truth_observed_at, now=now\):'; repl='elif False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and ACCOUNT_TRUTH_STALE' },
    @{ name='M3 additional managed accounts are accepted'; find='if len\(managed_account_ids\) > 1:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and MULTIPLE_MANAGED_ACCOUNTS' },
    @{ name='M4 unknown open orders are accepted'; find='if not open_orders_known:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and OPEN_ORDERS_UNKNOWN' },
    @{ name='M5 dirty reconciliation is accepted'; find='if not reconciliation_clean:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and RECONCILIATION_UNCLEAN' },
    @{ name='M6 kill latch is ignored'; find='if authorization_state\.kill_latch\.is_latched:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and KILL_LATCHED' },
    @{ name='M7 session arm is not required'; find='if not authorization_state\.session_armed:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and SESSION_NOT_ARMED' },
    @{ name='M8 invalid limits are accepted'; find='if not limits_valid:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and INVALID_LIMITS' },
    @{ name='M9 stale reconciliation is accepted'; find='elif not _fresh\(reconciliation_observed_at, now=now\):'; repl='elif False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and RECONCILIATION_STALE' },
    @{ name='M10 expired authorization is accepted'; find='elif not authorization\.is_valid_at\(now\):'; repl='elif False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and AUTHORIZATION_EXPIRED' },
    @{ name='M11 unknown account truth is accepted'; find='if not account_truth_known:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and ACCOUNT_TRUTH_UNKNOWN' },
    @{ name='M12 unknown market truth is accepted'; find='if not market_truth_known:'; repl='if False:'; select='any_missing_or_unsafe_startup_fact_blocks_proof and MARKET_TRUTH_UNKNOWN' },
    @{ name='M13 callers can forge a startup proof constructor'; find='raise LiveStartupError\("LiveStartupProof must be created by capture"\)'; repl='return object.__new__(cls)'; select='callers_cannot_construct_a_startup_proof_without_capture' },
    @{ name='M14 expired startup proof remains fresh at expiry'; find='self\.observed_at <= now < self\.expires_at'; repl='self.observed_at <= now <= self.expires_at'; select='startup_proof_captures_current_exact_account_facts_without_raw_account_id' },
    @{ name='M15 startup facts remain fresh at their expiry instant'; find='timedelta\(0\) <= age < LIVE_STARTUP_PROOF_TTL'; repl='timedelta(0) <= age <= LIVE_STARTUP_PROOF_TTL'; select='any_missing_or_unsafe_startup_fact_blocks_proof and ACCOUNT_TRUTH_STALE' }
)

$results = @()
foreach ($mutation in $mutations) {
    $original = Get-Text $domain
    $regex = [regex]::new($mutation.find)
    $matches = $regex.Matches($original)
    if ($matches.Count -ne 1) {
        $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=$false; Detail="expected 1 anchor, found $($matches.Count)" }
        continue
    }
    $backup = "$domain.live-startup-mutation-backup"
    Copy-Item -LiteralPath $domain -Destination $backup -Force
    try {
        Set-Text $domain ($regex.Replace($original, $mutation.repl, 1))
        $output = & $py -m pytest $testFile -q -k $mutation.select 2>&1
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
        Copy-Item -LiteralPath $backup -Destination $domain -Force
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
