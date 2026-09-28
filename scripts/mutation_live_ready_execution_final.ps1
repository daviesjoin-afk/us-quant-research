# Stage 3 Final exact-one mutation harness. Every mutation must make its named
# pytest target fail by assertion; dead patterns, syntax errors and process
# failures are reported as HARNESS-ERROR and fail the harness.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:QT_QPA_PLATFORM = "offscreen"
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
$domain = Join-Path $projectRoot "src\us_quant\trading\domain\execution_environment.py"
$composition = Join-Path $projectRoot "src\us_quant\trading\composition\execution.py"
$desktop = Join-Path $projectRoot "src\us_quant\desktop.py"
$risk = Join-Path $projectRoot "src\us_quant\trading\application\risk.py"
$execution = Join-Path $projectRoot "src\us_quant\trading\application\execution.py"
$dispatch = Join-Path $projectRoot "src\us_quant\trading\runtime\dispatch.py"
$runtime = Join-Path $projectRoot "src\us_quant\trading\runtime\trading.py"
$paperService = Join-Path $projectRoot "src\us_quant\trading\application\paper\service.py"
$brokerPort = Join-Path $projectRoot "src\us_quant\trading\ports\broker_execution.py"
$paperAdapter = Join-Path $projectRoot "src\us_quant\trading\adapters\ibkr\execution.py"
$behaviour = Join-Path $projectRoot "tests\test_execution_environment.py"
$core = Join-Path $projectRoot "tests\test_live_ready_execution_core.py"
$architecture = Join-Path $projectRoot "tests\test_live_ready_execution_architecture.py"
$closure = Join-Path $projectRoot "tests\test_live_ready_execution_closure.py"
$ibkrTests = Join-Path $projectRoot "tests\test_trading_ibkr_execution_adapter.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$mutations = @(
    @{ name='M1 LIVE true blocker becomes NONE'; file=$domain; find='blocker=ExecutionDeploymentBlocker\.LIVE_CANARY_GATES_REQUIRED'; repl='blocker=ExecutionDeploymentBlocker.NONE'; tests=@($behaviour); select=@('-k','deployment_truth_table') },
    @{ name='M2 LIVE true is allowed'; file=$domain; find='broker_submission_allowed=False,\s+blocker=ExecutionDeploymentBlocker\.LIVE_CANARY_GATES_REQUIRED'; repl="broker_submission_allowed=True,`n        blocker=ExecutionDeploymentBlocker.LIVE_CANARY_GATES_REQUIRED"; tests=@($behaviour); select=@('-k','deployment_truth_table') },
    @{ name='M3 BACKTEST becomes allowed'; file=$domain; find='if environment is Environment\.BACKTEST:\s+return ExecutionDeploymentDecision\(\s+environment=environment,\s+broker_submission_allowed=False,'; repl="if environment is Environment.BACKTEST:`n        return ExecutionDeploymentDecision(`n            environment=environment,`n            broker_submission_allowed=True,"; tests=@($behaviour); select=@('-k','deployment_truth_table') },
    @{ name='M4 denied factory constructs Paper adapter'; file=$composition; find='if \(\s+decision\.environment is not Environment\.PAPER\s+or not decision\.broker_submission_allowed\s+\):\s+raise ExecutionDeploymentError\(decision\.reason\)'; repl='if False: raise ExecutionDeploymentError(decision.reason)'; tests=@($behaviour); select=@('-k','denied_deployment_never_constructs_paper_adapter') },
    @{ name='M5 Desktop constructs adapter directly'; file=$desktop; find='build_execution_candidate_factory\('; repl='IBKRExecutionAdapter('; tests=@($architecture); select=@('-k','desktop_only_uses_the_gated_factory') },
    @{ name='M6 PaperTradingService branches on Environment'; file=$paperService; find='self\._order_service_factory = order_service_factory'; repl="self._order_service_factory = order_service_factory`n        if Environment.LIVE: pass"; tests=@($architecture); select=@('-k','paper_service_remains_environment_blind') },
    @{ name='M7 RiskApplication imports Environment'; file=$risk; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nfrom us_quant.trading.domain.common import Environment"; tests=@($architecture); select=@('-k','shared_execution_core_has_no_environment_import_or_branch') },
    @{ name='M8 ExecutionApplication imports Environment'; file=$execution; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nfrom us_quant.trading.domain.common import Environment"; tests=@($architecture); select=@('-k','shared_execution_core_has_no_environment_import_or_branch') },
    @{ name='M9 OrderDispatch branches on Environment'; file=$dispatch; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nfrom us_quant.trading.domain.common import Environment`n`nif Environment.LIVE: pass"; tests=@($architecture); select=@('-k','shared_execution_core_has_no_environment_import_or_branch') },
    @{ name='M10 TradingRuntime branches on Environment'; file=$runtime; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nfrom us_quant.trading.domain.common import Environment`n`nif Environment.LIVE: pass"; tests=@($architecture); select=@('-k','shared_execution_core_has_no_environment_import_or_branch') },
    @{ name='M11 success status hardcodes IBKR Paper'; file=$dispatch; find='f"已提交\{intent\.side\.order_text\}'; repl='f"已提交 IBKR Paper {intent.side.order_text}'; tests=@($architecture); select=@('-k','runtime_status_and_reason_literals_are_provider_neutral') },
    @{ name='M12 uncertain status hardcodes Paper'; file=$dispatch; find='"订单提交结果不确定'; repl='"Paper 订单提交结果不确定'; tests=@($architecture); select=@('-k','runtime_status_and_reason_literals_are_provider_neutral') },
    @{ name='M13 force-flat reason hardcodes Paper'; file=$runtime; find='"收盘前提交执行通道限价平仓"'; repl='"收盘前提交 Paper 限价平仓"'; tests=@($architecture); select=@('-k','runtime_status_and_reason_literals_are_provider_neutral') },
    @{ name='M14 Paper adapter accepts port 7497'; file=$paperAdapter; find='if config\.port != 4002:'; repl='if config.port not in {4002, 7497}:'; tests=@($ibkrTests); select=@('-k','only_local_paper_order_config_is_accepted') },
    @{ name='M15 Paper adapter accepts U account'; file=$paperAdapter; find='not accounts\[0\]\.upper\(\)\.startswith\("DU"\)'; repl="not accounts[0].upper().startswith((`"DU`", `"U`"))"; tests=@($ibkrTests); select=@('-k','managed_accounts_rejects_a_non_du_account') },
    @{ name='M16 production placeOrder leaks into runtime'; file=$dispatch; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nif False: broker.placeOrder(order)"; tests=@($architecture); select=@('-k','production_broker_submit_and_cancel_are_adapter_owned') },
    @{ name='M17 production cancelOrder leaks into runtime'; file=$dispatch; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nif False: broker.cancelOrder(order)"; tests=@($architecture); select=@('-k','production_broker_submit_and_cancel_are_adapter_owned') },
    @{ name='M18 BrokerExecutionPort grows live_submit'; file=$brokerPort; find='    def fills\(self\) -> tuple\[ExecutionFill, \.\.\.\]: \.\.\.'; repl="    def fills(self) -> tuple[ExecutionFill, ...]: ...`n`n    def live_submit(self) -> None: ..."; tests=@($architecture); select=@('-k','broker_execution_port_has_exact_provider_neutral_surface') },
    @{ name='M19 durable record moves after submit'; file=$execution; find='self\._repository\.record_intent\('; repl="self._broker.submit(reservation)`n        self._repository.record_intent("; tests=@($closure); select=@('-k','c16_durable_record_precedes_submit') },
    @{ name='M20 uncertain submission retries'; file=$execution; find='except ExecutionSubmissionUncertain as error:'; repl="except ExecutionSubmissionUncertain as error:`n            self._broker.submit(reservation)"; tests=@($architecture); select=@('-k','uncertain_submission_handler_does_not_retry') },
    @{ name='M21 explicit refusal gets a retry seam'; file=$dispatch; find='except Exception as error:'; repl="except Exception as error:`n            self.execution.submit_approved(proposal=proposal, decision=decision, execution_symbol=execution_symbol, session_id=session_id, reason=reason)"; tests=@($architecture); select=@('-k','order_dispatch_has_no_refusal_retry_or_fallback_call') },
    @{ name='M22 invalid flag validation is bypassed'; file=$domain; find='if not isinstance\(live_trading_enabled, bool\):'; repl='if False:'; tests=@($behaviour); select=@('-k','invalid_live_feature_flag_is_rejected') }
)

$results = @()
foreach ($mutation in $mutations) {
    $path = $mutation.file
    $original = Get-Text $path
    $backup = "$path.mutation-backup"
    Copy-Item -LiteralPath $path -Destination $backup -Force
    $caught = $null
    $detail = ""
    try {
        $regex = [regex]::new($mutation.find, [System.Text.RegularExpressions.RegexOptions]::None)
        $matches = $regex.Matches($original)
        if ($matches.Count -ne 1) {
            $caught = "HARNESS-ERROR"
            $detail = "pattern matched $($matches.Count) locations; expected exactly 1"
        }
        else {
            $mutated = $regex.Replace($original, $mutation.repl, 1)
            if ($mutated -eq $original) {
                $caught = "HARNESS-ERROR"
                $detail = "mutation reproduced original source"
            }
        }
        if ($null -eq $caught) {
            Set-Text $path $mutated
            $syntax = & $py -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $path 2>&1
            if ($LASTEXITCODE -ne 0) {
                $caught = "HARNESS-ERROR"
                $detail = "invalid Python syntax: $($syntax | Select-Object -Last 1)"
            }
            else {
                $arguments = @('-m','pytest','-q','-p','no:cacheprovider') + $mutation.tests + $mutation.select
                $output = & $py @arguments 2>&1
                $exitCode = $LASTEXITCODE
                $countLine = $output | Select-String -Pattern '\d+ (passed|failed)' | Select-Object -Last 1
                if ($exitCode -eq 5) {
                    $caught = 'HARNESS-ERROR'; $detail = 'no tests selected'
                }
                elseif ($exitCode -notin 0,1) {
                    $caught = 'HARNESS-ERROR'; $detail = "pytest exited $exitCode (not an assertion failure)"
                }
                elseif (-not $countLine) {
                    $caught = 'HARNESS-ERROR'; $detail = 'no pytest count line'
                }
                elseif ($exitCode -eq 1 -and -not ($output | Select-String -Pattern 'AssertionError|assert .* ==|assert .* is|DID NOT RAISE' -Quiet)) {
                    $caught = 'HARNESS-ERROR'; $detail = 'pytest failed without an assertion failure'
                }
                else {
                    $caught = ($exitCode -ne 0)
                    $detail = "$countLine"
                }
            }
        }
    }
    catch {
        $caught = 'ERROR'; $detail = $_.Exception.Message
    }
    finally {
        Copy-Item -LiteralPath $backup -Destination $path -Force
        Remove-Item -LiteralPath $backup -Force
    }
    $results += [pscustomobject]@{ Mutation=$mutation.name; Caught=$caught; Detail=$detail }
    Write-Host ("{0,-55} RED={1}  {2}" -f $mutation.name, $caught, $detail)
}

$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED: $($row.Mutation) -> $($row.Detail)" }
if ($uncaught.Count -gt 0) { exit 1 }
exit 0
