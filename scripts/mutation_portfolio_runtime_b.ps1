# Stage 5-B portfolio persistence mutation gate.
# Each mutation must be caught by a behavioral repository/domain assertion.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$py = Join-Path $projectRoot ".venv314\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$repo = Join-Path $projectRoot "src\us_quant\trading\adapters\sqlite\portfolio_repository.py"
$domain = Join-Path $projectRoot "src\us_quant\trading\domain\portfolio.py"
$tests = Join-Path $projectRoot "tests\test_portfolio_repository.py"
$mutations = @(
    @{ name='M1 attribution is omitted from durable payload'; file=$repo; find='for item in decision\.attribution'; repl='for item in ()'; select='round_trip_survives_repository_restart' },
    @{ name='M2 conflicting duplicate decision is accepted'; file=$repo; find='raise ValueError\("conflicting portfolio decision id"\)'; repl='return stored'; select='same_decision_payload_is_idempotent_and_conflict_is_refused' },
    @{ name='M3 stale revision is accepted'; file=$repo; find='if current\.revision != expected_revision:'; repl='if False:'; select='decision_update_uses_compare_and_swap_revision' },
    @{ name='M4 corrupt state is returned as empty'; file=$repo; find='raise PortfolioStoreUnreadable\("stored portfolio decision is unreadable"\) from exc'; repl='return None'; select='corrupt_json_row_is_unreadable_not_an_empty_ledger' },
    @{ name='M5 signed attribution is converted to absolute quantity'; file=$repo; find='"signed_requested_quantity": item\.signed_requested_quantity'; repl='"signed_requested_quantity": abs(item.signed_requested_quantity)'; select='round_trip_survives_repository_restart' },
    @{ name='M6 zero-net decision reconstructs an action'; file=$repo; find='if action_data is not None'; repl='if action_data is not None or value["net_quantity"] == 0'; select='zero_net_decision_persists_without_action' },
    @{ name='M7 domain symbol canonicalization is bypassed'; file=$domain; find='return value\.strip\(\)\.upper\(\)'; repl='return value'; select='round_trip_survives_repository_restart' },
    @{ name='M8 SQL key mismatch is accepted'; file=$repo; find='if \(\s+expected_decision_id is not None\s+and value\.get\("decision_id"\) != expected_decision_id\s+\):'; repl='if False:'; select='row_key_must_match_embedded_decision_id' }
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
    $backup = "$($mutation.file).stage5b-backup"
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
        $assertionFailure = $output | Select-String -Pattern 'AssertionError|DID NOT RAISE|PortfolioStoreUnreadable|PortfolioError|ValueError' -Quiet
        $caught = ($exitCode -eq 1 -and $assertionFailure)
        Write-Host ("RED={0} {1}" -f $caught, $mutation.name)
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
