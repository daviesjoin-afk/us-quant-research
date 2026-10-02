# Stage 6-E mutation gate: every semantic mutant must make its focused test RED.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'tests')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$py = (Get-Command python -ErrorAction Stop).Source
$mutations = @(
    @{ name='E-M01 candidate ID uses uuid'; file='src/us_quant/trading/domain/strategy_search.py'; find='return "scv-" + _digest('; repl='return "scv-" + str(__import__("uuid").uuid4()) + _digest('; test='tests/test_strategy_candidate_generation_architecture.py'; select='identity_helpers' },
    @{ name='E-M02 generation ID includes clock'; file='src/us_quant/trading/domain/strategy_search.py'; find='"deterministic_seed": deterministic_seed,'; repl='"deterministic_seed": deterministic_seed + datetime.now().isoformat(),'; test='tests/test_strategy_candidate_generation_architecture.py'; select='identity_helpers' },
    @{ name='E-M03 candidate ID includes ordinal'; file='src/us_quant/trading/domain/strategy_search.py'; find='(?s)(def candidate_version_id_for\(.*?"generator_version": generator_version,)'; repl='$1`n            "ordinal": policy_revision,'; regex=$true; test='tests/test_strategy_candidate_generation_architecture.py'; select='identity_helpers' },
    @{ name='E-M04 unordered candidate collection'; file='src/us_quant/trading/domain/strategy_search.py'; find='ordered = sorted('; repl='ordered = list('; test='tests/test_strategy_search_domain.py'; select='generator_is_deterministic' },
    @{ name='E-M05 deterministic seed ignored'; file='src/us_quant/trading/domain/strategy_search.py'; find='"deterministic_seed": policy.deterministic_seed,'; repl='"deterministic_seed": "ignored",'; test='tests/test_strategy_search_domain.py'; select='seed_changes_order' },
    @{ name='E-M06 maximum candidates ignored'; file='src/us_quant/trading/domain/strategy_search.py'; find='return tuple(ordered[: policy.maximum_candidates_per_generation])'; repl='return tuple(ordered)'; test='tests/test_strategy_search_domain.py'; select='policy_minimum_maximum_delta' },
    @{ name='E-M07 maximum delta ignored'; file='src/us_quant/trading/domain/strategy_search.py'; find='steps = int(rule.maximum_delta // rule.step)'; repl='steps = int(rule.maximum_delta // rule.step) + 1'; test='tests/test_strategy_search_domain.py'; select='delta_has_an_independent' },
    @{ name='E-M08 minimum bound ignored'; file='src/us_quant/trading/domain/strategy_search.py'; find='if not rule.minimum <= proposed <= rule.maximum:'; repl='if not proposed <= rule.maximum:'; test='tests/test_strategy_search_domain.py'; select='minimum_and_maximum_each' },
    @{ name='E-M09 maximum bound ignored'; file='src/us_quant/trading/domain/strategy_search.py'; find='if not rule.minimum <= proposed <= rule.maximum:'; repl='if not rule.minimum <= proposed:'; test='tests/test_strategy_search_domain.py'; select='minimum_and_maximum_each' },
    @{ name='E-M10 strategy validator bypassed'; file='src/us_quant/trading/domain/strategy_search.py'; find='validate_strategy_parameters('; repl='dict('; test='tests/test_strategy_search_domain.py'; select='relationally_invalid_neighbors' },
    @{ name='E-M11 bool scalar accepted as INTEGER'; file='src/us_quant/trading/domain/strategy_search.py'; find='if type(value) is not int:'; repl='if type(value) is not int and not isinstance(value, bool):'; test='tests/test_strategy_search_domain.py'; select='non_scalar_parent' },
    @{ name='E-M12 mutate parent parameters'; file='src/us_quant/trading/domain/strategy_search.py'; find='candidate_parameters = dict(parent_parameters)'; repl='candidate_parameters = parent_parameters'; test='tests/test_strategy_search_domain.py'; select='generator_is_deterministic' },
    @{ name='E-M13 duplicate hashes admitted'; file='src/us_quant/trading/domain/strategy_search.py'; find='by_parameter_hash[candidate_hash] = StrategyCandidateSpec('; repl='by_parameter_hash[f"{candidate_hash}:{rule.parameter_key}"] = StrategyCandidateSpec('; test='tests/test_strategy_search_domain.py'; select='duplicate_normalized' },
    @{ name='E-M14 parent-equal candidate admitted'; file='src/us_quant/trading/domain/strategy_search.py'; find='if candidate_hash == parent.parameter_hash or candidate_hash in by_parameter_hash:'; repl='if candidate_hash in by_parameter_hash:'; test='tests/test_strategy_search_domain.py'; select='parent_equal_normalized' },
    @{ name='E-M15 relationally invalid neighbor admitted'; file='src/us_quant/trading/domain/strategy_search.py'; find='(?ms)(except \(StrategyParameterError, ValueError, TypeError, OverflowError\):.*?^\s*)continue'; repl='$1normalized = candidate_parameters'; regex=$true; test='tests/test_strategy_search_domain.py'; select='relationally_invalid_neighbors' },
    @{ name='E-M16 candidate inherits parent status'; file='src/us_quant/trading/application/strategies.py'; find='(?s)(def clone_research_candidate\(.*?status=)StrategyStatus\.RESEARCH,'; repl='$1source.status,'; regex=$true; test='tests/test_strategy_candidate_evidence_isolation.py'; select='candidate_receives_none' },
    @{ name='E-M17 candidate inherits parent mode'; file='src/us_quant/trading/application/strategies.py'; find='(?s)(def clone_research_candidate\(.*?mode=)StrategyMode\.RESEARCH,'; repl='$1source.mode,'; regex=$true; test='tests/test_strategy_candidate_evidence_isolation.py'; select='candidate_receives_none' },
    @{ name='E-M18 candidate inherits gate flag'; file='src/us_quant/trading/application/strategies.py'; find='(?s)(def _create_version\(.*?gate_passed=)False,'; repl='$1source.gate_passed,'; regex=$true; test='tests/test_trading_strategy_application.py'; select='does_not_inherit_a_legacy_gate' },
    @{ name='E-M19 candidate app imports evidence store'; file='src/us_quant/trading/application/strategy_candidate_generation.py'; find='from us_quant.trading.domain.strategy import ('; repl='from us_quant.trading.adapters.sqlite.evidence_authentication_repository import SQLiteEvidenceAuthenticationRepository`nfrom us_quant.trading.domain.strategy import ('; test='tests/test_strategy_candidate_generation_architecture.py'; select='only_search_and_strategy' },
    @{ name='E-M20 active candidate limit ignored'; file='src/us_quant/trading/application/strategy_candidate_generation.py'; find='if len(active_ids | intended_ids) > policy.maximum_active_candidates:'; repl='if False:'; test='tests/test_strategy_candidate_generation.py'; select='resource_bounds' },
    @{ name='E-M21 total candidate limit ignored'; file='src/us_quant/trading/application/strategy_candidate_generation.py'; find='if len(lineage_ids) + len(intended_ids) > policy.maximum_total_candidates:'; repl='if False:'; test='tests/test_strategy_candidate_generation.py'; select='resource_bounds' },
    @{ name='E-M22 maximum generation ignored'; file='src/us_quant/trading/domain/strategy_search.py'; find='not 1 <= generation <= policy.maximum_generations'; repl='not 1 <= generation'; test='tests/test_strategy_search_domain.py'; select='wrong_strategy_and_generation' },
    @{ name='E-M23 cooldown ignored'; file='src/us_quant/trading/application/strategy_candidate_generation.py'; find='if current - latest < policy.generation_cooldown:'; repl='if False:'; test='tests/test_strategy_candidate_generation.py'; select='cooldown_and_generation_bounds' },
    @{ name='E-M24 retry does not reuse candidate'; file='src/us_quant/trading/application/strategy_candidate_generation.py'; find='child = self._strategies.get_version(spec.candidate_version_id)'; repl='child = None  # skip deterministic child lookup'; test='tests/test_strategy_candidate_generation.py'; select='crash_after_first_child' },
    @{ name='E-M25 mismatched child accepted'; file='src/us_quant/trading/application/strategy_candidate_generation.py'; find='if not equal:'; repl='if False:'; test='tests/test_strategy_candidate_generation.py'; select='mismatched_child_fails_closed' },
    @{ name='E-M26 generation payload not verified'; file='src/us_quant/trading/adapters/sqlite/strategy_search_repository.py'; find='if generation_semantic_payload(stored) != generation_semantic_payload(generation):'; repl='if False:'; test='tests/test_strategy_search_repository.py'; select='other_lineage_semantics' },
    @{ name='E-M27 active policy replaces exact revision'; file='src/us_quant/trading/application/strategy_candidate_generation.py'; find='policy = self._repository.get_policy(policy_id, policy_revision)'; repl='policy = self._repository.active_policy(policy_id)'; test='tests/test_strategy_candidate_generation.py'; select='explicit_policy_revision' },
    @{ name='E-M28 policy strategy mismatch accepted'; file='src/us_quant/trading/domain/strategy_search.py'; find='if parent.strategy_id != policy.strategy_id:'; repl='if False:'; test='tests/test_strategy_search_domain.py'; select='wrong_strategy_and_generation' },
    @{ name='E-M29 missing parent key silently ignored'; file='src/us_quant/trading/domain/strategy_search.py'; find='if rule.parameter_key not in parent_parameters:'; repl='if False:'; test='tests/test_strategy_search_domain.py'; select='silently_ignore' },
    @{ name='E-M30 empty generation not persisted'; file='src/us_quant/trading/application/strategy_candidate_generation.py'; find='return self._repository.record_generation(completed)'; repl='return completed'; test='tests/test_strategy_candidate_generation.py'; select='zero_candidate_generation' }
)

$baselineFiles = @(
    'tests/test_strategy_search_domain.py', 'tests/test_strategy_search_repository.py',
    'tests/test_strategy_candidate_generation.py', 'tests/test_strategy_candidate_generation_architecture.py',
    'tests/test_trading_strategy_application.py', 'tests/test_strategy_candidate_evidence_isolation.py'
)
$baseline = & $py -m pytest ($baselineFiles | ForEach-Object { Join-Path $projectRoot $_ }) -q 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host ($baseline -join "`n"); throw 'Baseline is not GREEN' }
$survivors = @()
$errors = @()
foreach ($mutation in $mutations) {
    $path = [IO.Path]::GetFullPath((Join-Path $projectRoot $mutation.file))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($projectRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'Mutation path escaped workspace' }
    $original = [IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    $anchor = $mutation.find.Replace("`r`n", "`n")
    $replacement = $mutation.repl.Replace("`r`n", "`n").Replace('`n', "`n")
    $isRegex = $mutation.Contains('regex') -and $mutation.regex
    $count = if ($isRegex) { [regex]::Matches($original, $anchor).Count } else { [regex]::Matches($original, [regex]::Escape($anchor)).Count }
    if ($count -ne 1) { $errors += "$($mutation.name): anchors=$count"; continue }
    try {
        $mutated = if ($isRegex) { [regex]::Replace($original, $anchor, $replacement, 1) } else { $original.Replace($anchor, $replacement) }
        [IO.File]::WriteAllText($path, $mutated, [Text.UTF8Encoding]::new($false))
        $cacheDir = Join-Path (Split-Path -Parent $path) '__pycache__'
        if (Test-Path -LiteralPath $cacheDir) {
            $stem = [IO.Path]::GetFileNameWithoutExtension($path)
            foreach ($cache in Get-ChildItem -LiteralPath $cacheDir -Filter "$stem.*.pyc" -File) { Remove-Item -LiteralPath $cache.FullName }
        }
        & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $path
        if ($LASTEXITCODE -ne 0) { $errors += "$($mutation.name): syntax error"; continue }
        $testPath = Join-Path $projectRoot $mutation.test
        $output = & $py -m pytest $testPath -q -k $mutation.select --tb=short 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 1 -and ($output | Select-String -Pattern 'FAILED|AssertionError|DID NOT RAISE|NameError|TypeError|ValueError|StrategyCandidateGenerationError|StrategySearchError' -Quiet) -and -not ($output | Select-String -Pattern 'no tests ran|ERROR collecting' -Quiet)) {
            Write-Host "RED=True $($mutation.name)"
        } elseif ($code -eq 0) {
            $survivors += $mutation.name
            Write-Host "SURVIVOR $($mutation.name)"
        } else {
            $errors += "$($mutation.name): exit=$code; $($output -join ' ')"
        }
    } finally {
        [IO.File]::WriteAllText($path, $original, [Text.UTF8Encoding]::new($false))
    }
}
Write-Host "mutations=$($mutations.Count) red=$($mutations.Count-$survivors.Count-$errors.Count) survivors=$($survivors.Count) harness_errors=$($errors.Count)"
foreach ($item in ($survivors + $errors)) { Write-Host $item }
if ($survivors.Count -gt 0 -or $errors.Count -gt 0) { exit 1 }
exit 0
