# Stage 6-A independent strategy evidence gate mutation harness.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$py = $env:US_QUANT_PYTHON
if (-not $py) { $py = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$evaluator = Join-Path $projectRoot "src\us_quant\trading\application\strategy_gate.py"
$projection = Join-Path $projectRoot "src\us_quant\trading\adapters\research_evidence.py"
$repository = Join-Path $projectRoot "src\us_quant\trading\adapters\sqlite\strategy_gate_repository.py"
$testsEvaluator = Join-Path $projectRoot "tests\test_strategy_gate_evaluator.py"
$testsRepository = Join-Path $projectRoot "tests\test_strategy_gate_repository.py"

$mutations = @(
    @{ name='M01 version mismatch becomes accepted'; file=$evaluator; find='if identity\.strategy_version_id != version\.version_id:'; repl='if False:'; tests=@($testsEvaluator); select='test_version_identity_mismatch_fails' },
    @{ name='M02 semver mismatch becomes accepted'; file=$evaluator; find='if identity\.strategy_semver != version\.semver:'; repl='if False:'; tests=@($testsEvaluator); select='test_version_identity_mismatch_fails' },
    @{ name='M03 parameter hash mismatch becomes accepted'; file=$evaluator; find='if identity\.parameter_hash != version\.parameter_hash:'; repl='if False:'; tests=@($testsEvaluator); select='test_version_identity_mismatch_fails' },
    @{ name='M04 blocked review decision becomes accepted'; file=$evaluator; find='or evidence\.decision != ELIGIBLE_REVIEW_DECISION'; repl='or False'; tests=@($testsEvaluator); select='test_review_decision_eligibility_blockers_and_origin_fail_closed' },
    @{ name='M05 blocking review failures ignored'; file=$evaluator; find='if evidence\.blocking_failures > 0:'; repl='if False:'; tests=@($testsEvaluator); select='test_review_decision_eligibility_blockers_and_origin_fail_closed' },
    @{ name='M06 captured stream requirement removed'; file=$evaluator; find='if evidence\.evidence_origins != \("captured_stream",\):'; repl='if False:'; tests=@($testsEvaluator); select='test_review_decision_eligibility_blockers_and_origin_fail_closed' },
    @{ name='M07 evaluator mutates lifecycle state'; file=$evaluator; find='if not isinstance\(policy, StrategyGatePolicy\):'; repl="object.__setattr__(version, `"gate_passed`", not version.gate_passed)`n        if not isinstance(policy, StrategyGatePolicy):"; tests=@($testsEvaluator); select='test_evaluation_does_not_change_strategy_lifecycle_fields' },
    @{ name='M08 conflicting immutable duplicate accepted'; file=$repository; find='raise StrategyGateRepositoryConflict\(\s*"evaluation id already has a different immutable payload"\s*\)'; repl='return'; tests=@($testsRepository); select='test_roundtrip_restart_idempotency_conflict_and_latest_order' },
    @{ name='M09 payload corruption becomes accepted'; file=$repository; find='if sha256\(payload_json\.encode\("utf-8"\)\)\.hexdigest\(\) != payload_hash:'; repl='if False:'; tests=@($testsRepository); select='test_corrupt_indexed_rows_fail_closed and payload_hash' },
    @{ name='M10 TargetedReview eligibility bypassed'; file=$projection; find='eligible_for_independent_review=result\.eligible_for_independent_review,'; repl='eligible_for_independent_review=True,'; tests=@($testsEvaluator); select='test_targeted_review_ineligible_flag_is_preserved_by_projection' },
    @{ name='M11 freshness transition reuses evaluation id'; file=(Join-Path $projectRoot "src\us_quant\trading\domain\strategy_gate.py"); find='"evaluated_at": evaluated_at\.astimezone\(timezone\.utc\)\.isoformat\(\),'; repl='"evaluated_at": None,'; tests=@($testsEvaluator); select='test_time_dependent_evaluations_have_distinct_stable_ids' }
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
        $failures += "$($mutation.name): expected one mutation anchor, found $($matches.Count)"
        continue
    }
    $backup = "$($mutation.file).stage6a-backup"
    Copy-Item -LiteralPath $mutation.file -Destination $backup -Force
    try {
        Set-Text $mutation.file ($regex.Replace($original, $mutation.repl, 1))
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $syntaxOutput = & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $mutation.file 2>&1
        $syntaxExit = $LASTEXITCODE
        $ErrorActionPreference = $previousPreference
        if ($syntaxExit -ne 0) {
            $failures += "$($mutation.name): syntax error: $($syntaxOutput -join ' ')"
            Write-Host "RED=False $($mutation.name)"
            continue
        }
        $output = & $py -m pytest @($mutation.tests) -q -k $mutation.select 2>&1
        $exitCode = $LASTEXITCODE
        $outputText = $output | Out-String
        $collectionError = $outputText -match 'ERROR collecting|Interrupted: [0-9]+ errors during collection|ModuleNotFoundError|ImportError|SyntaxError'
        $testFailure = $outputText -match '(?m)(^|\s)[0-9]+ failed([,\s]|$)'
        $expectedAssertion = $outputText -match 'AssertionError|Failed: DID NOT RAISE'
        $caught = ($exitCode -eq 1) -and $testFailure -and $expectedAssertion -and -not $collectionError
        Write-Host ("RED={0} {1}" -f [bool]$caught, $mutation.name)
        if (-not $caught) { $failures += "$($mutation.name): exit=$exitCode; $outputText" }
    }
    catch {
        $failures += "$($mutation.name): harness error: $_"
    }
    finally {
        $ErrorActionPreference = "Stop"
        Copy-Item -LiteralPath $backup -Destination $mutation.file -Force
        Remove-Item -LiteralPath $backup -Force
    }
}
Write-Host ("mutations={0} red={1} survivor_or_error={2}" -f $mutations.Count, ($mutations.Count - $failures.Count), $failures.Count)
foreach ($failure in $failures) { Write-Host "SURVIVED / HARNESS-ERROR: $failure" }
if ($failures.Count -gt 0) { exit 1 }
exit 0
