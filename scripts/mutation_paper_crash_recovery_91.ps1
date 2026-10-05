$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $projectRoot 'src') + ';' + (Join-Path $projectRoot 'tests')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$py = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
$mutations = @(
    @{ name='M01 durable decision resumed into Risk'; file='src/us_quant/trading/application/portfolio_runtime.py'; find='if previous is not None and previous.risk_outcome is None:'; repl='if False:'; test='tests/test_paper_crash_boundary_recovery.py'; select='durable_decision_without_order_truth' },
    @{ name='M02 durable decision halt flag cleared'; file='src/us_quant/trading/application/portfolio_runtime.py'; find='                                halt=True,'; repl='                                halt=False,'; test='tests/test_paper_crash_boundary_recovery.py'; select='durable_decision_without_order_truth' },
    @{ name='M03 risk-known recovered decision redispatched'; file='src/us_quant/trading/application/portfolio_runtime.py'; find='if previous is not None and previous.risk_outcome is not None:'; repl='if False:'; test='tests/test_paper_crash_boundary_recovery.py'; select='uncertain_submit_remains_fail_closed' },
    @{ name='M04 existing intent may be resubmitted'; file='src/us_quant/trading/application/execution.py'; find='if self._repository.intent(intent.order_id) is not None:'; repl='if False:'; test='tests/test_trading_execution_application.py'; select='submitting_the_same_order_twice' },
    @{ name='M05 duplicate idempotency key accepted'; file='src/us_quant/trading/application/execution.py'; find='if duplicate is not None and duplicate.order_id != intent.order_id:'; repl='if False:'; test='tests/test_trading_execution_application.py'; select='duplicate_idempotency_key_for_a_different_order' },
    @{ name='M06 uncertain broker error swallowed'; file='src/us_quant/trading/application/execution.py'; find='            self._broker.submit(reservation)'; repl='            pass'; test='tests/test_trading_execution_application.py'; select='uncertain_submission_carries_the_order' },
    @{ name='M07 intent omitted before send'; file='src/us_quant/trading/application/execution.py'; find='        self._repository.record_intent('; repl='        if False: self._repository.record_intent('; test='tests/test_trading_execution_application.py'; select='uncertain_submission_carries_the_order' },
    @{ name='M08 fill insert accepts duplicates'; file='src/us_quant/trading/adapters/sqlite/order_repository.py'; find='INSERT OR IGNORE INTO paper_execution('; repl='INSERT INTO paper_execution('; test='tests/test_trading_order_repository.py'; select='record_fill_is_idempotent_on_the_execution_id' },
    @{ name='M09 duplicate fill reports inserted'; file='src/us_quant/trading/adapters/sqlite/order_repository.py'; find='return cursor.rowcount == 1'; repl='return True'; test='tests/test_trading_order_repository.py'; select='record_fill_is_idempotent_on_the_execution_id' },
    @{ name='M10 fill broker id mismatch ignored'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='or fill.broker_order_id != order.broker_order_id'; repl='or False'; test='tests/test_portfolio_reconciliation.py'; select='fill_with_wrong_broker_order_id' },
    @{ name='M11 unknown order status accepted'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='if event is None or event.status is OrderStatus.UNKNOWN:'; repl='if False:'; test='tests/test_portfolio_reconciliation.py'; select='durable_intent_without_broker_status' },
    @{ name='M12 order without attribution accepted'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='if order.intent.order_id not in attribution_by_order:'; repl='if False:'; test='tests/test_portfolio_reconciliation.py'; select='local_order_without_portfolio_attribution' },
    @{ name='M13 missing decision attribution ignored'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='if record.order_id is not None and record.order_id not in attribution_by_order:'; repl='if False:'; test='tests/test_portfolio_reconciliation.py'; select='decision_order_reference_without_attribution' },
    @{ name='M14 stale broker/open order set ignored'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='if set(broker_open_by_id) != set(local_open_by_broker_id):'; repl='if False:'; test='tests/test_paper_crash_boundary_recovery.py'; select='broker_local_order_disagreement' },
    @{ name='M15 partial-fill mismatch ignored'; file='src/us_quant/trading/domain/portfolio_reconciliation.py'; find='event.filled != filled_total`n            or event.remaining != Decimal(intent.quantity) - filled_total'; repl='False`n            or False'; test='tests/test_portfolio_reconciliation.py'; select='partial_fill_event_totals_must_match' },
    @{ name='M16 performance source digest omits fills'; file='src/us_quant/trading/application/strategy_paper_performance.py'; find="'fills': sorted(order.fills, key=lambda x: (x.occurred_at, x.execution_id, x.order_id)),"; repl="'fills': [],"; test='tests/test_strategy_paper_performance_application.py'; select='source_digest_binds_fills_attribution_and_decision_revision' },
    @{ name='M17 missing performance policy accepted'; file='src/us_quant/trading/domain/strategy_paper_performance.py'; find='if policy is None:'; repl='if False:'; test='tests/test_strategy_paper_performance_application.py'; select='missing_policy_is_durable_fail' },
    @{ name='M18 digest excludes attributions'; file='src/us_quant/trading/application/strategy_paper_performance.py'; find="'attributions': sorted(attributions, key=canonical_json),"; repl="'attributions': [],"; test='tests/test_strategy_paper_performance_application.py'; select='source_digest_binds_fills_attribution_and_decision_revision' },
    @{ name='M19 restart workflow auto-arms'; file='src/us_quant/trading/runtime/workflow.py'; find='self._phase = PaperWorkflowPhase.IDLE'; repl='self._phase = PaperWorkflowPhase.RUNNING'; test='tests/test_runtime_workflow.py'; select='cancel_preparing_returns_to_idle' },
    @{ name='M20 restart digest changes with evaluation time'; file='src/us_quant/trading/application/strategy_paper_performance.py'; find="'window': (window_start, window_end),"; repl="'window': (window_start, window_end, evaluated_at),"; test='tests/test_paper_crash_boundary_recovery.py'; select='partial_fill_and_attribution_reconstruct_once' }
)
$baselineTargets = @(
    $mutations |
        ForEach-Object { "$($_.test)|$($_.select)" } |
        Sort-Object -Unique
)
foreach ($target in $baselineTargets) {
    $parts = $target.Split('|', 2)
    $testPath = Join-Path $projectRoot $parts[0]
    $selector = $parts[1]
    $baseline = & $py -m pytest $testPath -q -k $selector --tb=short 2>&1
    $code = $LASTEXITCODE
    $outputText = $baseline -join "`n"
    $executed = 0
    foreach ($match in [regex]::Matches($outputText, '(\d+) (passed|failed|error|errors|xfailed|xpassed)')) {
        $executed += [int]$match.Groups[1].Value
    }
    if ($code -ne 0 -or $executed -lt 1 -or $outputText -match 'no tests ran|ERROR collecting') {
        Write-Host $outputText
        throw "Baseline is not GREEN: $($parts[0]) -k $selector (exit=$code, executed=$executed)"
    }
    Write-Host "BASELINE GREEN $($parts[0]) -k $selector (executed=$executed)"
}
$survivors = @()
$errors = @()
foreach ($mutation in $mutations) {
    $path = [IO.Path]::GetFullPath((Join-Path $projectRoot $mutation.file))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($projectRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'Mutation path escaped workspace' }
    $originalBytes = [IO.File]::ReadAllBytes($path)
    $original = [IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    $anchor = $mutation.find.Replace('`n', "`n")
    $replacement = $mutation.repl.Replace('`n', "`n")
    $count = [regex]::Matches($original, [regex]::Escape($anchor)).Count
    if ($count -ne 1) { $errors += "$($mutation.name): anchors=$count"; continue }
    try {
        [IO.File]::WriteAllText($path, $original.Replace($anchor, $replacement), [Text.UTF8Encoding]::new($false))
        $output = & $py -m pytest (Join-Path $projectRoot $mutation.test) -q -k $mutation.select --tb=short 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 1 -and ($output | Select-String -Pattern 'FAILED|AssertionError|DID NOT RAISE|IntegrityError' -Quiet) -and -not ($output | Select-String -Pattern 'no tests ran|ERROR collecting' -Quiet)) {
            Write-Host "RED=True $($mutation.name)"
        } elseif ($code -eq 0) {
            $survivors += $mutation.name
            Write-Host "SURVIVOR $($mutation.name)"
        } else {
            $errors += "$($mutation.name): exit=$code; $($output -join ' ')"
        }
    } finally {
        [IO.File]::WriteAllBytes($path, $originalBytes)
    }
}
Write-Host "mutations=$($mutations.Count) red=$($mutations.Count-$survivors.Count-$errors.Count) survivors=$($survivors.Count) harness_errors=$($errors.Count)"
foreach ($item in ($survivors + $errors)) { Write-Host $item }
if ($survivors.Count -gt 0 -or $errors.Count -gt 0) { exit 1 }
exit 0
