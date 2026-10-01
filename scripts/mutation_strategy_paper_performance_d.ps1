# Stage 6-D meaningful mutations: baseline GREEN, each behavioral mutant RED.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'tests')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$py = (Get-Command python -ErrorAction Stop).Source
$mutations = @(
    @{ name='D01 first contribution only'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='        contributions,
        key=lambda entry:'; repl='        contributions[:1],
        key=lambda entry:'; test='tests/test_strategy_paper_performance_domain.py'; select='multi_strategy' },
    @{ name='D02 UUID replay ordering'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='in sorted(replay_batches):'; repl='in sorted(replay_batches, key=lambda x: x[2]):'; test='tests/test_strategy_paper_performance_domain.py'; select='multi_strategy' },
    @{ name='D03 drop pre-window history'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='and item.occurred_at <= window_end)'; repl='and window_start <= item.occurred_at <= window_end)'; test='tests/test_strategy_paper_performance_domain.py'; select='pre_window' },
    @{ name='D04 count pre-window notional'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='    fees_complete = True'; repl='    notional = sum((item.gross_traded_notional for item in observations if item.occurred_at < window_start), ZERO)
    fees_complete = True'; test='tests/test_strategy_paper_performance_domain.py'; select='pre_window' },
    @{ name='D05 missing fee as complete zero'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='fees_complete = fees_complete and item.allocated_fee is not None'; repl='fees_complete = True'; test='tests/test_strategy_paper_performance_domain.py'; select='missing_fee' },
    @{ name='D06 favorable cancels adverse'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='adverse += max(item.slippage, ZERO)'; repl='adverse += item.slippage'; test='tests/test_strategy_paper_performance_domain.py'; select='adverse_slippage' },
    @{ name='D07 count empty session'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='sessions.add(item.session_id)'; repl='sessions.add(''empty-session'')'; test='tests/test_strategy_paper_performance_domain.py'; select='declared_window' },
    @{ name='D08 use declared duration'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='last-first if first is not None and last is not None else timedelta(0)'; repl='window_end-window_start'; test='tests/test_strategy_paper_performance_domain.py'; select='declared_window' },
    @{ name='D09 ignore dirty reconciliation'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='if policy.require_clean_reconciliation and reconciliation.blockers:'; repl='if False:'; test='tests/test_strategy_paper_performance_domain.py'; select='dirty_reconciliation' },
    @{ name='D10 ignore stale reconciliation'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='or moment > evaluated_at or evaluated_at-moment > policy.maximum_reconciliation_age):'; repl='or moment > evaluated_at):'; test='tests/test_strategy_paper_performance_domain.py'; select='reconciliation_freshness' },
    @{ name='D11 partial sell is round trip'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='trips += int(item.completed_round_trip)'; repl='trips += int(True)'; test='tests/test_strategy_paper_performance_domain.py'; select='partial_sell' },
    @{ name='D12 reset window cost basis'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='basis[item.symbol] = item.cost_basis
    exposure'; repl='basis[item.symbol] = ZERO
    exposure'; test='tests/test_strategy_paper_performance_domain.py'; select='pre_window' },
    @{ name='D13 unstable fee residue'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='keys = sorted(weights)'; repl='keys = sorted(weights, reverse=True)'; test='tests/test_strategy_paper_performance_domain.py'; select='fee_rounding' },
    @{ name='D14 source digest ignores fills'; file='src/us_quant/trading/application/strategy_paper_performance.py'; find='''fills'': sorted(order.fills, key=lambda x: (x.occurred_at, x.execution_id, x.order_id)),'; repl='''fills'': [],'; test='tests/test_strategy_paper_performance_application.py'; select='source_digest' },
    @{ name='D15 source digest ignores attribution'; file='src/us_quant/trading/application/strategy_paper_performance.py'; find='''attributions'': sorted(attributions, key=canonical_json),'; repl='''attributions'': [],'; test='tests/test_strategy_paper_performance_application.py'; select='source_digest' },
    @{ name='D16 ignore policy revision'; file='src/us_quant/trading/application/strategy_paper_performance.py'; find='policy_revision=policy.revision if policy else None,'; repl='policy_revision=1 if policy else None,'; test='tests/test_strategy_paper_performance_application.py'; select='policy_revision' },
    @{ name='D17 insufficient sessions pass'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='if len(metrics.paper_session_ids) < policy.minimum_distinct_sessions:'; repl='if False:'; test='tests/test_strategy_paper_performance_domain.py'; select='sufficiency' },
    @{ name='D18 insufficient becomes fail'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='StrategyPaperPerformanceVerdict.INSUFFICIENT if insufficient else'; repl='StrategyPaperPerformanceVerdict.FAIL if insufficient else'; test='tests/test_strategy_paper_performance_domain.py'; select='sufficiency' },
    @{ name='D19 hide hard loss by insufficient'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='StrategyPaperPerformanceVerdict.FAIL if hard else'; repl='StrategyPaperPerformanceVerdict.FAIL if hard and not insufficient else'; test='tests/test_strategy_paper_performance_domain.py'; select='hard_loss' },
    @{ name='D20 ignore positive result'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='if not insufficient and policy.require_positive_net_result and metrics.net_realized_pnl <= ZERO:'; repl='if False:'; test='tests/test_strategy_paper_performance_domain.py'; select='positive_net' },
    @{ name='D21 missing policy accepted'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='hard.add(B.POLICY_MISSING)'; repl='pass'; test='tests/test_strategy_paper_performance_domain.py'; select='no_policy' },
    @{ name='D22 missing reconciliation accepted'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='hard.add(B.SOURCE_TRUTH_MISSING)'; repl='pass'; test='tests/test_strategy_paper_performance_domain.py'; select='source_missing' },
    @{ name='D23 drop gross notional'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='notional += item.gross_traded_notional'; repl='notional += ZERO'; test='tests/test_strategy_paper_performance_domain.py'; select='multi_strategy' },
    @{ name='D24 double deduct slippage'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='len(executions), trips, notional, pnl, fees, pnl - fees,'; repl='len(executions), trips, notional, pnl, fees, pnl - fees - total_slippage,'; test='tests/test_strategy_paper_performance_domain.py'; select='realized_net_drawdown' },
    @{ name='D25 ignore realized drawdown'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='drawdown = max(drawdown, peak - curve)'; repl='drawdown = ZERO'; test='tests/test_strategy_paper_performance_domain.py'; select='drawdown' },
    @{ name='D26 fill-count average exposure'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='integral / duration_seconds(window_end-window_start)'; repl='integral / Decimal(len(observations) or 1)'; test='tests/test_strategy_paper_performance_domain.py'; select='time_weighted' },
    @{ name='D27 source excludes pre-window fills'; file='src/us_quant/trading/application/strategy_paper_performance.py'; find='and item.occurred_at <= window_end)'; repl='and window_start <= item.occurred_at <= window_end)'; test='tests/test_strategy_paper_performance_application.py'; select='cutoff' },
    @{ name='D28 wall clock semantic ID'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='payload.pop(''evaluated_at'', None)'; repl='pass'; test='tests/test_strategy_paper_performance_application.py'; select='restart' },
    @{ name='D29 ignore payload hash'; file='src/us_quant/trading/adapters/sqlite/strategy_paper_performance_repository.py'; find='if sha256(text.encode(''utf-8'')).hexdigest() != row[''payload_hash'']:'; repl='if False:'; test='tests/test_strategy_paper_performance_repository.py'; select='corrupt_identity' },
    @{ name='D30 ignore indexed identity'; file='src/us_quant/trading/adapters/sqlite/strategy_paper_performance_repository.py'; find='if name not in (''payload_json'', ''payload_hash'') and row[name] != canonical_value(getattr(value, name)):'; repl='if False:'; test='tests/test_strategy_paper_performance_repository.py'; select='corrupt_identity' },
    @{ name='D31 CAS revision lost'; file='src/us_quant/trading/adapters/sqlite/strategy_paper_performance_repository.py'; find='if current != expected_current_revision:'; repl='if current is not None and expected_current_revision is not None and current > expected_current_revision:'; test='tests/test_strategy_paper_performance_repository.py'; select='policy_revisions' },
    @{ name='D32 immutable payload conflict ignored'; file='src/us_quant/trading/adapters/sqlite/strategy_paper_performance_repository.py'; find='if before != after:'; repl='if False:'; test='tests/test_strategy_paper_performance_repository.py'; select='same_id' },
    @{ name='D33 unsupported policy accepted'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='hard.add(B.POLICY_VERSION_UNSUPPORTED)'; repl='pass'; test='tests/test_strategy_paper_performance_domain.py'; select='unsupported_policy' },
    @{ name='D34 future fills included'; file='src/us_quant/trading/application/strategy_paper_performance.py'; find='through_at=window_end,'; repl='through_at=None,'; test='tests/test_strategy_paper_performance_application.py'; select='application_replay_stops' },
    @{ name='D35 count fills instead of sessions'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='tuple(sorted(sessions)),'; repl='tuple(sorted(executions)),'; test='tests/test_strategy_paper_performance_domain.py'; select='declared_window' },
    @{ name='D36 ignore attribution completeness'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='if policy.require_complete_attribution and not metrics.attribution_complete:'; repl='if False:'; test='tests/test_strategy_paper_performance_domain.py'; select='incomplete_attribution' },
    @{ name='D37 ignore hard loss threshold'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='if -metrics.net_realized_pnl > policy.maximum_cumulative_loss:'; repl='if False:'; test='tests/test_strategy_paper_performance_domain.py'; select='hard_loss' },
    @{ name='D38 ignore hard drawdown threshold'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='if metrics.max_realized_net_drawdown > policy.maximum_realized_net_drawdown:'; repl='if False:'; test='tests/test_strategy_paper_performance_domain.py'; select='drawdown_limit' }
)
$baseline = & $py -m pytest (Join-Path $projectRoot 'tests/test_strategy_paper_performance_domain.py') (Join-Path $projectRoot 'tests/test_strategy_paper_performance_application.py') (Join-Path $projectRoot 'tests/test_strategy_paper_performance_repository.py') -q 2>&1
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
        if ($code -eq 1 -and ($output | Select-String -Pattern 'AssertionError|DID NOT RAISE|ValueError' -Quiet)) {
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
