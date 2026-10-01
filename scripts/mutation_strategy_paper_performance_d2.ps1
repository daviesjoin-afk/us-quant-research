# Stage 6-D meaningful mutations: baseline GREEN, each behavioral mutant RED.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'tests')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$py = (Get-Command python -ErrorAction Stop).Source
$mutations = @(
    @{ name='D2-01 failure ignored'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='and paper_performance.verdict is StrategyPaperPerformanceVerdict.FAIL'; repl='and False'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='fail_is_a_named' },
    @{ name='D2-02 insufficient pauses'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='and paper_performance.verdict is StrategyPaperPerformanceVerdict.FAIL'; repl='and paper_performance.verdict is not StrategyPaperPerformanceVerdict.PASS'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='pass_insufficient' },
    @{ name='D2-03 promotion gated by performance'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='if action is not StrategyLifecycleAction.PAUSE:'; repl='if False:'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='initial_promotion' },
    @{ name='D2-04 cross version accepted'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='if paper_performance.strategy_version_id != version.version_id:'; repl='if False:'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='cross_version' },
    @{ name='D2-05 future fact accepted'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='paper_performance.evaluated_at > decided_at'; repl='False'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='future_or_stale' },
    @{ name='D2-06 stale fact accepted'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='decided_at - paper_performance.evaluated_at > policy.maximum_evidence_age'; repl='False'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='future_or_stale' },
    @{ name='D2-07 latest fact not consumed'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='paper_performance = current'; repl='paper_performance = None'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='service_loads_durable' },
    @{ name='D2-08 noncurrent supplied fact accepted'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='if paper_performance is not None and current != paper_performance:'; repl='if False:'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='service_rejects_noncurrent' },
    @{ name='D2-09 identity not bound'; file='src/us_quant/trading/domain/strategy_lifecycle.py'; find='material["paper_performance_evaluation_id"] = paper_performance_evaluation_id'; repl='pass'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='fail_is_a_named' },
    @{ name='D2-10 link omitted from payload'; file='src/us_quant/trading/adapters/sqlite/strategy_lifecycle_repository.py'; find='payload["paper_performance_evaluation_id"] = decision.paper_performance_evaluation_id'; repl='pass'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='service_loads_durable' },
    @{ name='D2-11 rehashed link accepted'; file='src/us_quant/trading/adapters/sqlite/strategy_lifecycle_repository.py'; find='if expected_id != decision.decision_id and ('; repl='if False and ('; test='tests/test_strategy_paper_performance_lifecycle.py'; select='rehashed_performance' },
    @{ name='D2-12 stale fact vetoes other trigger'; file='src/us_quant/trading/application/strategy_lifecycle.py'; find='performance_issues.add(StrategyLifecycleBlocker.PAPER_PERFORMANCE_NOT_CURRENT)'; repl='structural.add(StrategyLifecycleBlocker.PAPER_PERFORMANCE_NOT_CURRENT)'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='invalid_performance_cannot_veto' },
    @{ name='D2-13 erased link bypasses identity'; file='src/us_quant/trading/adapters/sqlite/strategy_lifecycle_repository.py'; find='or (decision.decision_id.startswith("sld-")'; repl='or (False'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='erased_performance_link' },
    @{ name='D2-14 old controller hash rejected'; file='src/us_quant/trading/adapters/sqlite/strategy_lifecycle_repository.py'; find='return legacy_id == decision_id'; repl='return False'; test='tests/test_strategy_paper_performance_lifecycle.py'; select='pre_repair_controller_hash' }
)
$baseline = & $py -m pytest (Join-Path $projectRoot 'tests/test_strategy_paper_performance_lifecycle.py') -q 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host ($baseline -join "`n"); throw 'Baseline is not GREEN' }
$survivors = @()
$errors = @()
foreach ($mutation in $mutations) {
    $path = [IO.Path]::GetFullPath((Join-Path $projectRoot $mutation.file))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($projectRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'Mutation path escaped workspace' }
    $original = [IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    $anchor = $mutation.find.Replace("`r`n", "`n")
    $count = [regex]::Matches($original, [regex]::Escape($anchor)).Count
    if ($count -ne 1) { $errors += "$($mutation.name): anchors=$count"; continue }
    try {
        [IO.File]::WriteAllText($path, $original.Replace($anchor,$mutation.repl.Replace("`r`n","`n")), [Text.UTF8Encoding]::new($false))
        $cacheDir = Join-Path (Split-Path -Parent $path) '__pycache__'
        if (Test-Path -LiteralPath $cacheDir) {
            $stem = [IO.Path]::GetFileNameWithoutExtension($path)
            foreach ($cache in Get-ChildItem -LiteralPath $cacheDir -Filter "$stem.*.pyc" -File) { Remove-Item -LiteralPath $cache.FullName }
        }
        & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $path
        if ($LASTEXITCODE -ne 0) { $errors += "$($mutation.name): syntax error"; continue }
        $output = & $py -m pytest (Join-Path $projectRoot $mutation.test) -q -k $mutation.select --tb=short 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 1 -and ($output | Select-String -Pattern 'AssertionError|DID NOT RAISE|ValueError|StrategyLifecycleRepositoryError' -Quiet)) {
            Write-Host "RED=True $($mutation.name)"
        } elseif ($code -eq 0) {
            $survivors += $mutation.name
            Write-Host "SURVIVOR $($mutation.name)"
        } else {
            $errors += "$($mutation.name): exit=$code; $($output -join ' ')"
        }
    } finally {
        [IO.File]::WriteAllText($path,$original,[Text.UTF8Encoding]::new($false))
    }
}
Write-Host "mutations=$($mutations.Count) red=$($mutations.Count-$survivors.Count-$errors.Count) survivors=$($survivors.Count) harness_errors=$($errors.Count)"
foreach ($item in ($survivors + $errors)) { Write-Host $item }
if ($survivors.Count -gt 0 -or $errors.Count -gt 0) { exit 1 }
exit 0
