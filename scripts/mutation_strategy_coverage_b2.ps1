# Stage 6-B2 strategy evidence coverage mutation harness.
#
# Each mutation breaks one security-relevant branch in production code and
# asserts that a specific test turns RED.  A survivor means the suite does not
# actually pin the property it claims to.
#
# Run with -ValidateAnchors to check every regex anchors exactly once without
# mutating anything.
#
# Note on quoting: single-quoted PowerShell strings do NOT expand `n, so every
# multi-line replacement below is written with double quotes and `" escapes.

param([switch]$ValidateAnchors)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$py = $env:US_QUANT_PYTHON
if (-not $py) { $py = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$domain = Join-Path $projectRoot "src\us_quant\trading\domain\strategy_coverage.py"
$application = Join-Path $projectRoot "src\us_quant\trading\application\strategy_coverage.py"
$repository = Join-Path $projectRoot "src\us_quant\trading\adapters\sqlite\strategy_coverage_repository.py"
$composition = Join-Path $projectRoot "src\us_quant\trading\composition\strategy_coverage.py"
$runtimeConfig = Join-Path $projectRoot "src\us_quant\trading\runtime\config.py"

$tDomain = Join-Path $projectRoot "tests\test_strategy_coverage_domain.py"
$tEvaluator = Join-Path $projectRoot "tests\test_strategy_coverage_evaluator.py"
$tRepository = Join-Path $projectRoot "tests\test_strategy_coverage_repository.py"
$tArchitecture = Join-Path $projectRoot "tests\test_strategy_coverage_architecture.py"

$defaultPolicyInstance = "DEFAULT_COVERAGE_POLICY = StrategyCoveragePolicy(`n    policy_id=`"default`", revision=1, policy_version=`"evidence-coverage-v1`",`n    required_symbols=(`"AAPL`",), required_universe_hash=`"u`", required_code_hash=`"c`",`n    min_distinct_review_runs=1, min_distinct_data_hashes=1,`n    maximum_evidence_age=None, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),`n)`n`n`n"

$mutations = @(
    # -- policy validation -------------------------------------------------
    @{ name='M01 empty required-symbol universe accepted'; file=$domain; find='if not isinstance\(self\.required_symbols, tuple\) or not self\.required_symbols:'; repl='if not isinstance(self.required_symbols, tuple):'; tests=@($tDomain); select='test_policy_rejects_an_empty_required_symbol_universe' },
    @{ name='M02 repeated required symbols accepted'; file=$domain; find='if len\(set\(self\.required_symbols\)\) != len\(self\.required_symbols\):'; repl='if False:'; tests=@($tDomain); select='test_policy_rejects_repeated_required_symbols' },
    @{ name='M03 nonpositive policy revision accepted'; file=$domain; find='if type\(self\.revision\) is not int or self\.revision < 1:'; repl='if False:'; tests=@($tDomain); select='test_policy_rejects_a_nonpositive_revision' },
    @{ name='M04 zero distinct-evidence minimum accepted'; file=$domain; find='if type\(value\) is not int or value < 1:'; repl='if False:'; tests=@($tDomain); select='test_policy_rejects_a_zero_minimum' },
    @{ name='M05 PASS without naming its policy'; file=$domain; find='if self\.policy_id is None or self\.policy_revision is None:\s*\n\s*raise ValueError\("PASS requires the policy it was evaluated against"\)'; repl="if False:`n                raise ValueError(`"PASS requires the policy it was evaluated against`")"; tests=@($tDomain); select='test_pass_requires_a_policy_and_items' },
    @{ name='M06 PASS without admitted evidence'; file=$domain; find='            if not self\.items:'; repl='            if False:'; tests=@($tDomain); select='test_pass_requires_a_policy_and_items' },
    @{ name='M07 blockers not deduplicated'; file=$domain; find='tuple\(sorted\(set\(self\.blockers\), key=lambda item: item\.value\)\)'; repl='tuple(self.blockers)'; tests=@($tDomain); select='test_blockers_are_canonicalised_and_deduplicated' },
    @{ name='M08 evaluation identity ignores the evidence set'; file=$domain; find='"items": sorted\([\s\S]*?\n        \),'; repl='"items": [],'; tests=@($tDomain); select='test_evaluation_identity_moves_with_the_admitted_evidence' },
    @{ name='M09 evaluation identity ignores blockers'; file=$domain; find='"blockers": sorted\(\{item\.value for item in blockers\}\),'; repl='"blockers": [],'; tests=@($tDomain); select='test_two_failures_with_different_reasons_are_different_claims' },
    @{ name='M10 policy codec lets a bad timestamp escape'; file=$domain; find='try:\s*\n\s*parsed_created_at = datetime\.fromisoformat\(created_at\)\s*\n\s*except ValueError as error:\s*\n\s*raise CoveragePolicyMalformed\("policy created_at is not a timestamp"\) from error'; repl='parsed_created_at = datetime.fromisoformat(created_at)'; tests=@($tDomain); select='test_policy_codec_refuses_an_unparseable_created_at' },
    @{ name='M11 a ready-made default policy appears'; file=$domain; find='__all__ = \['; repl="$defaultPolicyInstance" + '__all__ = ['; tests=@($tDomain); select='test_policy_module_defines_no_authorising_default_instance' },
    @{ name='M12 a policy field gains a default'; file=$domain; find='    maximum_evidence_age: timedelta \| None\s*\n    created_at: datetime'; repl="    maximum_evidence_age: timedelta | None`n    created_at: datetime = None"; tests=@($tDomain); select='test_every_policy_field_is_required' },

    # -- the coverage decision --------------------------------------------
    @{ name='M13 a missing policy no longer fails closed'; file=$application; find='if not isinstance\(policy, StrategyCoveragePolicy\):'; repl='if False:'; tests=@($tEvaluator); select='test_missing_policy_fails_closed' },
    @{ name='M14 unsupported policy version accepted'; file=$application; find='if policy\.policy_version not in SUPPORTED_COVERAGE_POLICY_VERSIONS:'; repl='if False:'; tests=@($tEvaluator); select='test_unsupported_policy_version_fails_closed' },
    @{ name='M15 policy universe binding removed'; file=$application; find='if policy\.required_universe_hash != version\.universe_hash:'; repl='if False:'; tests=@($tEvaluator); select='test_policy_universe_hash_mismatch_fails_closed' },
    @{ name='M16 policy code binding removed'; file=$application; find='if policy\.required_code_hash != version\.code_hash:'; repl='if False:'; tests=@($tEvaluator); select='test_policy_code_hash_mismatch_fails_closed' },
    @{ name='M17 whole-universe coverage requirement removed'; file=$application; find='if not required <= set\(covered\):'; repl='if False:'; tests=@($tEvaluator); select='test_single_symbol_pass_never_covers_the_whole_universe' },
    @{ name='M18 distinct-review-run minimum removed'; file=$application; find='if len\(\{item\.review_run_id for item in admitted\}\) < policy\.min_distinct_review_runs:'; repl='if False:'; tests=@($tEvaluator); select='test_insufficient_distinct_review_runs_fails_closed' },
    @{ name='M19 distinct-data-hash minimum removed'; file=$application; find='if len\(\{item\.data_hash for item in admitted\}\) < policy\.min_distinct_data_hashes:'; repl='if False:'; tests=@($tEvaluator); select='test_insufficient_distinct_data_hashes_fails_closed' },
    @{ name='M20 a gate FAIL is admitted'; file=$application; find='if gate\.verdict is not StrategyGateVerdict\.PASS:'; repl='if False:'; tests=@($tEvaluator); select='test_gate_failure_excludes_that_evidence' },
    @{ name='M21 the gate must describe this evidence'; file=$application; find='if gate\.review_run_id != authenticated\.review_run_id:'; repl='if False:'; tests=@($tEvaluator); select='test_gate_for_a_different_review_excludes_that_evidence' },
    @{ name='M22 a revoked key still covers'; file=$application; find='if key\.trust_status is EvidenceKeyTrustStatus\.REVOKED:'; repl='if False:'; tests=@($tEvaluator); select='test_key_revoked_after_authentication_fails_coverage' },
    @{ name='M23 an unknown key is not reported'; file=$application; find='blockers\.add\(StrategyCoverageBlocker\.UNKNOWN_AUTHENTICATION_KEY\)'; repl='pass'; tests=@($tEvaluator); select='test_unknown_key_fails_coverage' },
    @{ name='M24 an unavailable trust root is not reported'; file=$application; find='blockers\.add\(StrategyCoverageBlocker\.TRUST_ROOT_UNAVAILABLE\)'; repl='pass'; tests=@($tEvaluator); select='test_missing_trust_root_fails_coverage' },
    @{ name='M25 duplicate evidence counts twice'; file=$application; find='if authenticated\.review_run_id in seen_review_runs:'; repl='if False:'; tests=@($tEvaluator); select='test_duplicate_review_identity_fails_closed' },
    @{ name='M26 the evidence age bound is ignored'; file=$application; find='policy\.maximum_evidence_age is not None\s*\n\s*and moment - generated_at > policy\.maximum_evidence_age'; repl='False'; tests=@($tEvaluator); select='test_stale_evidence_fails_closed' },
    @{ name='M27 semver binding removed'; file=$application; find='if authenticated\.strategy_semver != version\.semver:'; repl='if False:'; tests=@($tEvaluator); select='test_version_semver_mismatch_fails_closed' },
    @{ name='M28 parameter binding removed'; file=$application; find='if authenticated\.parameter_hash != version\.parameter_hash:'; repl='if False:'; tests=@($tEvaluator); select='test_version_parameter_hash_mismatch_fails_closed' },
    @{ name='M29 out-of-scope evidence counts toward coverage'; file=$application; find='if authenticated\.symbol not in required:'; repl='if False:'; tests=@($tEvaluator); select='test_evidence_for_an_unrequired_symbol_cannot_cover_a_required_one' },
    @{ name='M30 coverage mutates lifecycle state'; file=$application; find='blockers: set\[StrategyCoverageBlocker\] = set\(\)'; repl="object.__setattr__(version, `"gate_passed`", True)`n        blockers: set[StrategyCoverageBlocker] = set()"; tests=@($tEvaluator); select='test_coverage_does_not_change_strategy_lifecycle_fields' },

    # -- architecture -----------------------------------------------------
    @{ name='M31 evaluator gains a hardcoded threshold'; file=$application; find='class StrategyCoverageEvaluator:'; repl="_MINIMUM_SYMBOLS = 1`n`n`nclass StrategyCoverageEvaluator:"; tests=@($tArchitecture); select='test_c18_the_policy_is_the_only_source_of_coverage_thresholds' },
    @{ name='M32 coverage names a statistical threshold'; file=$application; find='class StrategyCoverageEvaluator:'; repl="_STATISTICS_OWNER = `"MAXIMUM_PBO`"`n`n`nclass StrategyCoverageEvaluator:"; tests=@($tArchitecture); select='test_c02_coverage_never_names_a_statistical_threshold' },
    @{ name='M33 coverage becomes promotion authority'; file=$application; find='class StrategyCoverageEvaluator:'; repl="_PROMOTION_FIELD = `"gate_passed`"`n`n`nclass StrategyCoverageEvaluator:"; tests=@($tArchitecture); select='test_c11_coverage_never_becomes_promotion_authority' },
    @{ name='M34 application imports the coverage adapter'; file=$application; find='from us_quant\.trading\.domain\.evidence_auth import \('; repl="from us_quant.trading.adapters.sqlite.strategy_coverage_repository import SQLiteStrategyCoverageRepository  # noqa: F401`nfrom us_quant.trading.domain.evidence_auth import ("; tests=@($tArchitecture); select='test_c06_application_reaches_only_domain_and_ports' },
    @{ name='M43 composition binds an execution surface'; file=$composition; find='from us_quant\.trading\.application\.strategy_coverage import StrategyCoverageEvaluator'; repl="from us_quant.trading.application.risk import RiskApplication  # noqa: F401`nfrom us_quant.trading.application.strategy_coverage import StrategyCoverageEvaluator"; tests=@($tArchitecture); select='test_c13_coverage_composition_binds_no_execution_surface' },
    @{ name='M44 a runtime module consults coverage'; file=$runtimeConfig; find='from __future__ import annotations'; repl="from __future__ import annotations`nfrom us_quant.trading.domain.strategy_coverage import StrategyCoveragePolicy  # noqa: F401"; tests=@($tArchitecture); select='test_c09_no_protected_layer_imports_the_coverage_machinery' },

    # -- persistence ------------------------------------------------------
    @{ name='M35 policy compare-and-set removed'; file=$repository; find='if current != expected_current_revision:'; repl='if False:'; tests=@($tRepository); select='test_cas_refuses_when_the_stored_history_has_a_hole' },
    @{ name='M36 revision may skip ahead'; file=$repository; find='if policy\.revision != expected_next:'; repl='if False:'; tests=@($tRepository); select='test_revision_must_be_exactly_one_greater' },
    @{ name='M37 policy indexed cross-check removed'; file=$repository; find='policy\.policy_id != policy_id\s*\n\s*or policy\.revision != revision\s*\n\s*or policy\.policy_version != policy_version\s*\n\s*or policy\.created_at\.isoformat\(\) != created_at'; repl='False'; tests=@($tRepository); select='test_corrupt_policy_indexed_columns_fail_closed' },
    @{ name='M38 evaluation payload hash check removed'; file=$repository; find='if sha256\(payload_json\.encode\("utf-8"\)\)\.hexdigest\(\) != payload_hash:'; repl='if False:'; tests=@($tRepository); select='test_corrupt_evaluation_payload_hash_fails_closed' },
    @{ name='M39 evaluation indexed cross-check removed'; file=$repository; find='if indexed != stored:'; repl='if False:'; tests=@($tRepository); select='test_corrupt_indexed_columns_fail_closed' },
    @{ name='M40 conflicting duplicate accepted'; file=$repository; find='raise StrategyCoverageRepositoryConflict\(\s*\n\s*"evaluation id already has a different immutable payload"\s*\n\s*\)'; repl='return'; tests=@($tRepository); select='test_same_evaluation_id_with_different_payload_conflicts' },
    @{ name='M41 retry idempotency broken by the clock'; file=$repository; find='payload\.pop\("evaluated_at", None\)'; repl='None'; tests=@($tRepository); select='test_semantic_retry_is_idempotent_and_keeps_first_timestamp' },
    @{ name='M42 newest-first ordering inverted'; file=$repository; find='return tuple\(sorted\(values, key=_sort_key, reverse=True\)\)'; repl='return tuple(sorted(values, key=_sort_key, reverse=False))'; tests=@($tRepository); select='test_newest_first_ordering' }
)

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

if ($ValidateAnchors) {
    $bad = 0
    foreach ($mutation in $mutations) {
        $count = ([regex]::new($mutation.find)).Matches((Get-Text $mutation.file)).Count
        if ($count -ne 1) { $bad++ }
        Write-Host ("{0,3}  {1}" -f $count, $mutation.name)
    }
    Write-Host "anchors=$($mutations.Count) not_exactly_one=$bad"
    if ($bad -gt 0) { exit 1 }
    exit 0
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
    $backup = "$($mutation.file).stage6b2-backup"
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
        $caught = ($exitCode -eq 1) -and $testFailure -and -not $collectionError
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
