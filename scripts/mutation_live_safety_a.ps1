# Stage 4-A Live safety foundation exact-one mutation harness.
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
$domain = Join-Path $projectRoot "src\us_quant\trading\domain\live_safety.py"
$adapter = Join-Path $projectRoot "src\us_quant\trading\adapters\sqlite\live_safety_repository.py"
$testFile = Join-Path $projectRoot "tests\test_live_safety.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$mutations = @(
    @{ name='M1 restart restores session arm'; file=$domain; find='return replace\(self, session_armed=False\)'; repl='return replace(self, session_armed=True)'; select='clearing_kill_latch_does_not_restore_session_arm' },
    @{ name='M2 expired authorization remains valid'; file=$domain; find='elif not authorization\.is_valid_at\(now\):'; repl='elif False:'; select='invalid_authorization_account_or_limits_block_session_arm' },
    @{ name='M3 account fingerprint mismatch is ignored'; file=$domain; find='if authorization\.expected_account_fingerprint != account_fingerprint:'; repl='if False:'; select='invalid_authorization_account_or_limits_block_session_arm' },
    @{ name='M4 canary limits do not gate arm'; file=$domain; find='blockers\.extend\(authorization\.approved_canary_limits\.blockers\(\)\)'; repl='pass  # mutation'; select='invalid_authorization_account_or_limits_block_session_arm' },
    @{ name='M5 operator confirmation is optional'; file=$domain; find='if not operator_confirmed:'; repl='if False:'; select='zero_defaults_and_confirmation_are_fail_closed' },
    @{ name='M6 kill latch does not block new exposure'; file=$domain; find='return operation not in \{'; repl='return operation in {'; select='kill_latch_survives_restart_and_only_blocks_exposure_increase' },
    @{ name='M7 stale repository writer overwrites newer state'; file=$adapter; find='if type\(actual_revision\) is not int or actual_revision != expected_revision:'; repl='if False:'; select='repository_compare_and_swap_refuses_stale_writer' },
    @{ name='M8 zero defaults permit session arm'; file=$domain; find='if not value\.is_finite\(\) or value <= 0:'; repl='if False:'; select='invalid_authorization_account_or_limits_block_session_arm' }
)

$results = @()
foreach ($mutation in $mutations) {
    $path = $mutation.file
    $original = Get-Text $path
    $regex = [regex]::new($mutation.find)
    $matches = $regex.Matches($original)
    if ($matches.Count -ne 1) {
        $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=$false; Detail="expected 1 anchor, found $($matches.Count)" }
        continue
    }
    $backup = "$path.live-safety-mutation-backup"
    Copy-Item -LiteralPath $path -Destination $backup -Force
    try {
        Set-Text $path ($regex.Replace($original, $mutation.repl, 1))
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
        Copy-Item -LiteralPath $backup -Destination $path -Force
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
