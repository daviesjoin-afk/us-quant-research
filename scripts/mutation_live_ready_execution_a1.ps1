# Stage 3-A exact-one mutation harness. Every mutation must make its named
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
$ibkrTests = Join-Path $projectRoot "tests\test_trading_ibkr_execution_adapter.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

$mutations = @(
    @{ name='M1 BACKTEST opens a broker channel'; file=$domain; find='if environment is Environment\.BACKTEST:\s+return ExecutionDeploymentDecision\(\s+environment=environment,\s+broker_submission_allowed=False,'; repl="if environment is Environment.BACKTEST:`n        return ExecutionDeploymentDecision(`n            environment=environment,`n            broker_submission_allowed=True,"; tests=@($behaviour); select=@('-k','deployment_truth_table') },
    @{ name='M2 PAPER is blocked'; file=$domain; find='if environment is Environment\.PAPER:\s+return ExecutionDeploymentDecision\(\s+environment=environment,\s+broker_submission_allowed=True,'; repl="if environment is Environment.PAPER:`n        return ExecutionDeploymentDecision(`n            environment=environment,`n            broker_submission_allowed=False,"; tests=@($behaviour); select=@('-k','deployment_truth_table') },
    @{ name='M3 LIVE flag false enables broker submission'; file=$domain; find='if not live_trading_enabled:\s+return ExecutionDeploymentDecision\(\s+environment=environment,\s+broker_submission_allowed=False,'; repl="if not live_trading_enabled:`n        return ExecutionDeploymentDecision(`n            environment=environment,`n            broker_submission_allowed=True,"; tests=@($behaviour); select=@('-k','deployment_truth_table') },
    @{ name='M4 LIVE flag true enables broker submission'; file=$domain; find='broker_submission_allowed=False,\s+blocker=ExecutionDeploymentBlocker\.LIVE_ADAPTER_UNAVAILABLE,\s+reason="Live execution is not implemented in Stage 3"'; repl="broker_submission_allowed=True,`n        blocker=ExecutionDeploymentBlocker.NONE,`n        reason=`"Live execution is not implemented in Stage 3`""; tests=@($behaviour); select=@('-k','deployment_truth_table') },
    @{ name='M5 PAPER factory is incorrectly blocked'; file=$composition; find='if not decision\.broker_submission_allowed:'; repl='if decision.broker_submission_allowed:'; tests=@($behaviour); select=@('-k','paper_factory_selects_only_existing_paper_adapter') },
    @{ name='M6 denied environment falls back to Paper adapter'; file=$composition; find='raise ExecutionDeploymentError\(decision\.reason\)'; repl='return _build_paper_execution_candidate(config, repository=repository, extended_hours_enabled=extended_hours_enabled)'; tests=@($behaviour); select=@('-k','denied_deployment_never_constructs_paper_adapter') },
    @{ name='M7 Desktop bypasses gated factory'; file=$desktop; find='build_execution_candidate_factory\('; repl='build_execution_candidate('; tests=@($architecture); select=@('-k','desktop_only_uses_the_gated_factory') },
    @{ name='M8 RiskApplication imports Environment'; file=$risk; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nfrom us_quant.trading.domain.common import Environment"; tests=@($architecture); select=@('-k','shared_execution_core_has_no_environment_import_or_branch') },
    @{ name='M9 ExecutionApplication imports Environment'; file=$execution; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nfrom us_quant.trading.domain.common import Environment"; tests=@($architecture); select=@('-k','shared_execution_core_has_no_environment_import_or_branch') },
    @{ name='M10 OrderDispatch imports Environment'; file=$dispatch; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nfrom us_quant.trading.domain.common import Environment"; tests=@($architecture); select=@('-k','shared_execution_core_has_no_environment_import_or_branch') },
    @{ name='M11 TradingRuntime imports Environment'; file=$runtime; find='from __future__ import annotations'; repl="from __future__ import annotations`n`nfrom us_quant.trading.domain.common import Environment"; tests=@($architecture); select=@('-k','shared_execution_core_has_no_environment_import_or_branch') },
    @{ name='M12 PaperTradingService reads Environment'; file=$paperService; find='self\._order_service_factory = order_service_factory'; repl="self._order_service_factory = order_service_factory`n        if Environment.LIVE: pass"; tests=@($architecture); select=@('-k','paper_service_remains_environment_blind') },
    @{ name='M13 BrokerExecutionPort grows a Live method'; file=$brokerPort; find='    def fills\(self\) -> tuple\[ExecutionFill, \.\.\.\]: \.\.\.'; repl="    def fills(self) -> tuple[ExecutionFill, ...]: ...`n`n    def live_submit(self) -> None: ..."; tests=@($architecture); select=@('-k','broker_execution_port_has_exact_provider_neutral_surface') },
    @{ name='M14 Paper adapter accepts Live port 7497'; file=$paperAdapter; find='if config\.port != 4002:'; repl='if config.port not in {4002, 7497}:'; tests=@($ibkrTests); select=@('-k','only_local_paper_order_config_is_accepted') },
    @{ name='M15 Paper adapter accepts U account'; file=$paperAdapter; find='not accounts\[0\]\.upper\(\)\.startswith\("DU"\)'; repl="not accounts[0].upper().startswith((`"DU`", `"U`"))"; tests=@($ibkrTests); select=@('-k','managed_accounts_rejects_a_non_du_account') },
    @{ name='M16 order is submitted before durable correlation'; file=$execution; find='self\._repository\.record_intent\('; repl='if False: self._repository.record_intent('; tests=@($core); select=@('-k','alternate_provider_runs_through_dispatch_and_durable_execution') },
    @{ name='M17 uncertain alternate submission is retried'; file=$execution; find='except ExecutionSubmissionUncertain as error:'; repl="except ExecutionSubmissionUncertain as error:`n            self._broker.submit(reservation)"; tests=@($core); select=@('-k','uncertain_alternate_submission_halts_without_retry') }
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
