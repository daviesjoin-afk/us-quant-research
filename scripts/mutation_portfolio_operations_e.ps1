# Stage 5-E portfolio operations mutation gate.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$py = $env:US_QUANT_PYTHON
if (-not $py) { $py = Join-Path $projectRoot ".venv314\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

$operations = Join-Path $projectRoot "src\us_quant\trading\application\portfolio_operations.py"
$portfolioRuntime = Join-Path $projectRoot "src\us_quant\trading\application\portfolio_runtime.py"
$snapshot = Join-Path $projectRoot "src\us_quant\trading\application\portfolio_snapshot.py"
$allocator = Join-Path $projectRoot "src\us_quant\trading\application\portfolio.py"
$paperEngine = Join-Path $projectRoot "src\us_quant\trading\runtime\portfolio_paper.py"
$registry = Join-Path $projectRoot "src\us_quant\trading\composition\runtime.py"
$queries = Join-Path $projectRoot "src\us_quant\desktop_v2\orchestration\paper\queries.py"
$orchestrator = Join-Path $projectRoot "src\us_quant\desktop_v2\orchestration\paper\orchestrator.py"
$desktop = Join-Path $projectRoot "src\us_quant\desktop.py"
$page = Join-Path $projectRoot "src\us_quant\desktop_v2\pages\execution\page.py"
$testsOperations = Join-Path $projectRoot "tests\test_portfolio_operations.py"
$testsRuntime = Join-Path $projectRoot "tests\test_portfolio_runtime.py"
$testsSnapshot = Join-Path $projectRoot "tests\test_portfolio_snapshot.py"
$testsCycle = Join-Path $projectRoot "tests\test_portfolio_cycle.py"
$testsPaperEngine = Join-Path $projectRoot "tests\test_portfolio_paper_engine.py"
$testsLaunch = Join-Path $projectRoot "tests\test_desktop_paper_launch_orchestrator.py"
$testsWiring = Join-Path $projectRoot "tests\test_desktop_v2_paper_wiring.py"
$testsArchitecture = Join-Path $projectRoot "tests\test_portfolio_architecture.py"

$mutations = @(
    @{ name='M1 missing portfolio plan accepted'; file=$operations; find='if plan is None:\s*raise PortfolioPlanRefused\("no portfolio operating plan is configured"\)'; repl="if False:`n            pass"; tests=@($testsOperations); select='test_plan_missing_refuses_and_save_round_trips_with_audit' },
    @{ name='M2 unknown strategy accepted'; file=$operations; find='if version is None:'; repl='if False:'; tests=@($testsOperations); select='test_unknown_selected_strategy_refuses_portfolio_plan' },
    @{ name='M3 unallocated selected strategy accepted'; file=$operations; find='if allocation is None or not allocation.enabled or allocation.capital_weight <= 0 or allocation.max_capital <= 0 or allocation.max_gross_exposure <= 0:'; repl='if False:'; tests=@($testsOperations); select='test_selected_but_unallocated_strategy_refuses_portfolio_plan' },
    @{ name='M4 stale plan CAS accepted'; file=$operations; find='if type\(expected_revision\) is not int or expected_revision != current_revision:'; repl='if False:'; tests=@($testsOperations); select='test_plan_stale_cas_and_active_session_edits_are_refused' },
    @{ name='M5 plan drift during launch ignored'; file=$orchestrator; find='if not portfolio_matches:'; repl='if False:'; tests=@($testsWiring); select='test_portfolio_plan_revision_drift_during_connect_disposes_candidate' },
    @{ name='M6 production Paper constructs the legacy TradingRuntime'; file=$desktop; find='session_id = session\.session_id'; repl="session_id = session.session_id`n        TradingRuntime()"; tests=@($testsArchitecture); select='test_production_paper_composes_one_portfolio_engine_and_no_single_strategy_fallback' },
    @{ name='M7 one PortfolioRuntime per strategy allowed'; file=$registry; find='if current is not None and current is not runtime:'; repl='if False:'; tests=@($testsOperations); select='test_one_account_cannot_register_a_second_portfolio_runtime' },
    @{ name='M8 reconciliation blocker ignored'; file=$snapshot; find='if not result\.can_open_exposure:'; repl='if False:'; tests=@($testsSnapshot); select='test_unexplained_broker_position_blocks_snapshot_and_exposure' },
    @{ name='M9 zero-net reaches Risk'; file=$portfolioRuntime; find='or decision\.action is None'; repl='or False'; tests=@($testsRuntime); select='test_zero_net_and_hold_never_reach_risk' },
    @{ name='M10 Risk rejection reaches Execution'; file=$portfolioRuntime; find='if not risk_decision\.approved:'; repl='if False:'; tests=@($testsRuntime); select='test_risk_reject_is_persisted_and_never_submitted' },
    @{ name='M11 dispatch halt allows a later action'; file=$portfolioRuntime; find='if dispatch\.halt:\s*break'; repl="if False:`n                    break"; tests=@($testsRuntime); select='test_dispatch_halt_stops_later_actions_and_recovered_halt_stays_stopped' },
    @{ name='M12 active plan edit accepted'; file=$operations; find='if self\._active_session\(\):'; repl='if False:'; tests=@($testsOperations); select='test_plan_stale_cas_and_active_session_edits_are_refused' },
    @{ name='M13 nonzero restart state accepted'; file=$queries; find='if state\.positions:'; repl='if False and state.positions:'; tests=@($testsLaunch); select='test_a_broker_gate_refuses_without_publishing' },
    @{ name='M14 strategy SELL ownership removed'; file=$allocator; find='if quantity_delta < 0 and \('; repl='if False and ('; tests=@($testsRuntime); select='test_strategy_cannot_sell_another_strategys_position' },
    @{ name='M15 partial-fill attribution uses a second rule'; file=$portfolioRuntime; find='execution_contributions_for_quantity\(\s*decision,\s*risk_decision\.approved_quantity\s*\)'; repl='()'; tests=@($testsRuntime); select='test_risk_trim_is_allocated_across_execution_attribution' },
    @{ name='M16 Desktop page persists plan directly'; file=$page; find='self\.portfolio_plan_save_requested\.emit\('; repl='self.portfolio_plan_save_requested.save_editor('; tests=@($testsOperations); select='test_page_source_has_no_trading_authority_imports' },
    @{ name='M17 autonomous kill allows new entries'; file=(Join-Path $projectRoot "src\us_quant\trading\runtime\portfolio_cycle.py"); find='if allow_entries and not self\._autonomous_entries_allowed\(\):'; repl='if False:'; tests=@($testsCycle); select='test_persisted_autonomy_kill_blocks_new_entries_before_portfolio_runtime' }
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
    $backup = "$($mutation.file).stage5e-backup"
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
        $collectionError = $outputText -match 'ERROR collecting|Interrupted: [0-9]+ errors during collection|ModuleNotFoundError|ImportError'
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
