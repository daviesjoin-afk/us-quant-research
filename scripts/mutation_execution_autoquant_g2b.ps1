# Mutation verification for G2-B (execution / AutoQuant orchestration).
#
# Each entry breaks one property this round established, then asserts that the
# guard or behaviour test which names that property actually fails.  A mutation
# that survives is a hole: either the guard is not testing what it claims, or
# the property is not locked.
#
# The harness distinguishes three outcomes, and only `caught=True` counts as a
# caught mutation:
#
#   caught=True           the named tests ran and failed -- RED, as required;
#   caught=HARNESS-ERROR  the regex matched nothing, the mutation produced
#                         invalid Python, pytest exited 5 ("no tests collected"),
#                         or the output had no "N passed|failed" line.  Any of
#                         those is a hole in *this* script, and reporting one as
#                         "caught" would let a broken mutation look like a
#                         working guard;
#   caught=ERROR          an unexpected exception while mutating or restoring.
#
# Anchors are text lines, which is exactly how the earlier rounds lost mutants
# silently (E3's M27 twice, E2's M4/M9): any round that edits ``desktop.py`` or
# an orchestration package must re-run every harness in this directory.  A
# pattern that matches nothing is reported as HARNESS-ERROR and fails the script,
# so a dead anchor can never masquerade as a green gate.
#
# The find patterns are deliberately ASCII-only: the Chinese operator copy is
# captured with ``[^"]*`` wildcards and re-emitted through ``$1``/``$2`` groups,
# so this script never depends on how a host reads non-ASCII script bytes.
# ``find`` uses single-quoted PowerShell strings (regex metacharacters pass
# through untouched) and ``repl`` uses double-quoted ones with backtick
# escapes (```n`` newline, ```` `$ ```` literal dollar for capture groups).
#
# Run:
#
#     .\scripts\mutation_execution_autoquant_g2b.ps1

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot

$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONUTF8 = "1"

function Resolve-Python {
    # 3.14 is the primary interpreter; the fallbacks keep this runnable on a
    # machine where the primary venv has another directory name.
    foreach ($candidate in @(
        $env:USQUANT_PYTHON,
        (Join-Path $projectRoot ".venv314\Scripts\python.exe"),
        (Join-Path $projectRoot ".venv313\Scripts\python.exe"),
        (Join-Path $projectRoot ".venv\Scripts\python.exe")
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if (-not $onPath) {
        Write-Host @"
No Python interpreter found: none of .venv314 / .venv313 / .venv exists here and
``python`` is not on PATH.

    .\scripts\bootstrap_windows.ps1    # create the environment

Then re-run this script, or set ``USQUANT_PYTHON`` to an interpreter that has the
``desktop`` and ``test`` extras installed.
"@ -ForegroundColor Red
        exit 2
    }
    return $onPath.Source
}

$py = Resolve-Python

& $py -c "import pytest, PySide6" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "The interpreter '$py' cannot import pytest / PySide6; install the ``desktop`` and ``test`` extras into it, or set ``USQUANT_PYTHON`` to one that has them." -ForegroundColor Red
    exit 2
}

$desktopPath = Join-Path $projectRoot "src\us_quant\desktop.py"
$executionDir = Join-Path $projectRoot "src\us_quant\desktop_v2\orchestration\execution"
$orchestratorPath = Join-Path $executionDir "orchestrator.py"
$queriesPath = Join-Path $executionDir "queries.py"
$modelsPath = Join-Path $executionDir "models.py"

$g2bGuards = Join-Path $projectRoot "tests/test_desktop_execution_architecture.py"
$behaviour = Join-Path $projectRoot "tests/test_desktop_execution_orchestration.py"
$wiring = Join-Path $projectRoot "tests/test_desktop_v2_execution_wiring.py"
$route = Join-Path $projectRoot "tests/test_desktop_execution_route_characterization.py"
$scannerWiring = Join-Path $projectRoot "tests/test_desktop_research_scanner_wiring.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText(
        $path, $text, (New-Object System.Text.UTF8Encoding($false))
    )
}

#: Line/indent-preserving edits.  ``find`` always starts at a line's own
#: indentation and ``repl`` always repeats it, so a mutation cannot change a
#: block's nesting by accident.
$mutations = @(
    @{
        name = 'M1  the window re-acquires the launch-busy flag'
        file = $desktopPath
        find = '        self\.runtime_supervisor = RuntimeSupervisor\(\)'
        repl = "        self._launch_busy = False`n        self.runtime_supervisor = RuntimeSupervisor()"
        tests = @($g2bGuards)
        select = @("-k", "window_holds_no_execution_route_state or declares_no_shortlist_alias")
    },
    @{
        name = 'M2  the window re-acquires the candidate shortlist'
        file = $desktopPath
        find = '        self\.task_controller = DesktopTaskController\[TaskThread\]\(\)'
        repl = "        self.auto_quant_candidates: tuple = ()`n        self.task_controller = DesktopTaskController[TaskThread]()"
        tests = @($g2bGuards)
        select = @("-k", "window_holds_no_execution_route_state or declares_no_shortlist_alias")
    },
    @{
        name = 'M3  the window forwards the shortlist as a property'
        file = $desktopPath
        find = '    def _apply_execution_subscription\(self, symbols: object\) -> None:'
        repl = "    @property`n    def auto_quant_candidates(self):`n        return self.execution_orchestrator.candidates`n`n    def _apply_execution_subscription(self, symbols: object) -> None:"
        tests = @($g2bGuards)
        select = @("-k", "declares_no_shortlist_alias")
    },
    @{
        name = 'M4  the window renders the execution page directly again'
        file = $desktopPath
        find = '        self\.execution_page = ExecutionPage\(palette=self\.theme\)'
        repl = "        self.execution_page = ExecutionPage(palette=self.theme)`n        self.execution_page.render_candidates(None)"
        tests = @($g2bGuards)
        select = @("-k", "window_calls_no_execution_page_orchestration_method or window_only_hands_the_execution_page_its_palette")
    },
    @{
        name = 'M5  an unrelated task failure clears the execution lock'
        file = $desktopPath
        find = '        self\.runtime_events_orchestrator\.record\(\r?\n            severity="error",\r?\n            component="task",'
        repl = "        self.execution_orchestrator._launch_busy = False`n        self.runtime_events_orchestrator.record(`n            severity=`"error`",`n            component=`"task`","
        tests = @($g2bGuards, $behaviour)
        select = @("-k", "generic_task_failure_does_not_touch_the_execution_route or unrelated_task_failure_leaves_the_route_untouched")
    },
    @{
        name = 'M6  an unrelated worker finish repaints the execution route'
        file = $desktopPath
        find = '            self\.runtime_events_orchestrator\.notify_task_count_changed\(\)\r?\n\r?\n    def _task_cancelled'
        repl = "            self.runtime_events_orchestrator.notify_task_count_changed()`n        self.execution_orchestrator.refresh_controls()`n`n    def _task_cancelled"
        tests = @($g2bGuards, $behaviour)
        select = @("-k", "generic_worker_release_does_not_repaint_the_route or unrelated_worker_finish_repaints_no_route")
    },
    @{
        name = 'M7  the route caches the market snapshot'
        file = $orchestratorPath
        find = '        stream = self\._providers\.market_snapshot\(\)'
        repl = "        self._market_snapshot = self._providers.market_snapshot()`n        stream = self._market_snapshot"
        tests = @($g2bGuards)
        select = @("-k", "keeps_only_route_local_state")
    },
    @{
        name = 'M8  the route caches Paper retained presentation'
        file = $orchestratorPath
        find = '        session = self\._paper\.presentation'
        repl = "        self._presentation = self._paper.presentation`n        session = self._presentation"
        tests = @($g2bGuards)
        select = @("-k", "keeps_only_route_local_state")
    },
    @{
        name = 'M9  the retained presentation becomes a launch gate'
        file = $orchestratorPath
        find = '            or self\._paper\.launch_attempt_in_flight'
        repl = "            or self._paper.presentation is not None`n            or self._paper.launch_attempt_in_flight"
        tests = @($g2bGuards)
        select = @("-k", "presentation_is_read_only_on_the_render_path")
    },
    @{
        name = 'M10 the route reads a Paper phase itself'
        file = $orchestratorPath
        find = '            or self\._paper\.launch_attempt_in_flight'
        repl = "            or self._paper.phase() is not None"
        tests = @($g2bGuards)
        select = @("-k", "paper_port_is_exactly_the_declared_seams or preparation_transitions_are_delegated")
    },
    @{
        name = 'M11 the route imports another capability orchestrator'
        file = $orchestratorPath
        find = 'from us_quant\.desktop_v2\.pages\.execution\.presenter import \('
        repl = "from us_quant.desktop_v2.orchestration.paper import PaperOrchestrator`nfrom us_quant.desktop_v2.pages.execution.presenter import ("
        tests = @($g2bGuards)
        select = @("-k", "imports_no_other_capability")
    },
    @{
        name = 'M12 the route names a Paper workflow type'
        file = $orchestratorPath
        find = 'from us_quant\.desktop_v2\.pages\.execution\.presenter import \('
        repl = "from us_quant.trading.runtime.workflow_state import PaperWorkflowPhase`nfrom us_quant.desktop_v2.pages.execution.presenter import ("
        tests = @($g2bGuards)
        select = @("-k", "names_no_paper_type")
    },
    @{
        name = 'M13 the route names the risk / execution composition'
        file = $orchestratorPath
        find = 'from us_quant\.desktop_v2\.pages\.execution\.presenter import \('
        repl = "from us_quant.trading.application.risk import RiskApplication`nfrom us_quant.desktop_v2.pages.execution.presenter import ("
        tests = @($g2bGuards)
        select = @("-k", "names_no_risk_or_execution_application")
    },
    @{
        name = 'M14 candidate sizing runs on the research scenario capital'
        file = $orchestratorPath
        find = '            paper_capital, self\._page\.capital_limit\(\)'
        repl = "            self._providers.research_scenario_capital(), self._page.capital_limit()"
        tests = @($behaviour)
        select = @("-k", "shortlist_is_sized_on_the_bounded_paper_capital")
    },
    @{
        name = 'M15 the requested limit may enlarge Paper capital'
        file = $queriesPath
        find = '        return min\(paper_capital, requested_limit\)'
        repl = "        return max(paper_capital, requested_limit)"
        tests = @($behaviour)
        select = @("-k", "capital_limit_may_only_shrink_the_paper_figure")
    },
    @{
        name = 'M16 fewer than three candidates still reach READY'
        file = $orchestratorPath
        find = '        if len\(candidates\) < MINIMUM_CANDIDATES:'
        repl = "        if False:"
        tests = @($behaviour)
        select = @("-k", "too_few_candidates_never_reach_ready")
    },
    @{
        name = 'M17 a reference symbol becomes a tradable candidate'
        file = $queriesPath
        find = '        if symbol in excluded:\r?\n            continue'
        repl = "        if False:`n            continue"
        tests = @($behaviour)
        select = @("-k", "reference_symbols_never_become_tradable_candidates or shortlist_refuses_an_extra_reference_row")
    },
    @{
        name = 'M18 a duplicate probe starts a second probe'
        file = $orchestratorPath
        find = '        if self\._channel_probe_inflight:\r?\n            return'
        repl = "        if False:`n            return"
        tests = @($behaviour)
        select = @("-k", "second_probe_request_is_ignored")
    },
    @{
        name = 'M19 the launch lock ignores the probe flag'
        file = $orchestratorPath
        find = '            or self\._channel_probe_inflight\r?\n            or self\._paper\.launch_attempt_in_flight'
        repl = "            or self._paper.launch_attempt_in_flight"
        tests = @($behaviour)
        select = @("-k", "probe_locks_the_launch_controls or launch_lock_reads_paper_facts_and_local_flags")
    },
    @{
        name = 'M20 a rejected confirmation still starts Paper'
        file = $orchestratorPath
        find = '            self\._page\.set_arm_confirmed\(False\)\r?\n            return'
        repl = "            pass"
        tests = @($behaviour)
        select = @("-k", "declining_the_launch_clears_the_arm_and_starts_nothing")
    },
    @{
        name = 'M21 the combo replaces the selection service as truth'
        file = $orchestratorPath
        find = '            self\._selection\.select\(purpose, str\(version_id\)\)'
        repl = "            pass"
        tests = @($behaviour)
        select = @("-k", "selecting_a_strategy_writes_the_service_once or keeps_the_selection_service_as_the_only_writer")
    },
    @{
        name = 'M22 a failed preparation does not release the busy flag'
        file = $orchestratorPath
        find = '    def _preparation_failed\(self, _message: str\) -> None:\r?\n        """[^"]*"""\r?\n\r?\n        self\._cancel_preparation_if_active\(\)\r?\n        self\._set_launch_busy\(False\)'
        repl = "    def _preparation_failed(self, _message: str) -> None:`n`n        self._cancel_preparation_if_active()"
        tests = @($behaviour, $g2bGuards)
        select = @("-k", "failed_scan_cancels_preparation_and_releases_busy or every_route_task_releases_its_own_state")
    },
    @{
        name = 'M23 an unadmitted scan task keeps PREPARING'
        file = $orchestratorPath
        find = '        if not started:\r?\n            self\._paper\.cancel_preparation\(\)\r?\n            self\._set_launch_busy\(False\)'
        repl = "        if not started:`n            self._set_launch_busy(False)"
        tests = @($behaviour)
        select = @("-k", "unadmitted_scan_task_gives_preparation_back")
    },
    @{
        name = 'M24 the shortlist is published but readiness is not'
        file = $orchestratorPath
        find = '        self\._publish_readiness_inputs\(\)\r?\n        tally = queries\.preflight_tally'
        repl = "        tally = queries.preflight_tally"
        tests = @($behaviour)
        select = @("-k", "preflight_publishes_the_readiness_fact_first or successful_shortlist_retains_once_and_announces_everything")
    },
    @{
        name = 'M25 a session finalized does not clear the arm'
        file = $orchestratorPath
        find = '        self\._page\.render_execution_health\(SAFE_END_HEALTH\)\r?\n        self\._page\.set_arm_confirmed\(False\)'
        repl = "        self._page.render_execution_health(SAFE_END_HEALTH)"
        tests = @($behaviour)
        select = @("-k", "finalized_session_reports_and_clears_the_arm")
    },
    @{
        name = 'M26 a held order channel is probed anyway'
        file = $orchestratorPath
        find = '        if self\._paper\.order_service_held or self\._paper\.runtime_active:'
        repl = "        if False:"
        tests = @($behaviour)
        select = @("-k", "held_order_channel_needs_no_probe or live_session_needs_no_probe")
    },
    @{
        name = 'M27 the finished scan is not queued for history'
        file = $orchestratorPath
        find = '        scheduled = self\._providers\.schedule_history\(\r?\n            self\._providers\.universe\(\)\r?\n        \)'
        repl = "        scheduled = 0"
        tests = @($behaviour)
        select = @("-k", "successful_scan_is_adopted_once_and_queues_history")
    },
    @{
        name = 'M28 the finished scan is never adopted into the capability'
        file = $orchestratorPath
        find = '        self\._providers\.adopt_scan\(result\)'
        repl = "        pass"
        tests = @($behaviour)
        select = @("-k", "successful_scan_is_adopted_once_and_queues_history")
    },
    @{
        name = 'M29 the shortlist is not retained'
        file = $orchestratorPath
        find = '        self\._candidates = candidates'
        repl = "        self._candidates = ()"
        tests = @($behaviour)
        select = @("-k", "successful_shortlist_retains_once_and_announces_everything")
    },
    @{
        name = 'M30 a god object is introduced'
        file = $orchestratorPath
        find = 'class ExecutionOrchestrator\(QObject\):'
        repl = "class ExecutionManager:`n    pass`n`n`nclass ExecutionOrchestrator(QObject):"
        tests = @($g2bGuards)
        select = @("-k", "no_god_object_was_introduced")
    },
    @{
        name = 'M31 the provider group starts taking capability handles'
        file = $modelsPath
        find = '    universe: Callable\[\[\], Any\]'
        repl = "    universe: Any"
        tests = @($g2bGuards)
        select = @("-k", "route_holds_no_capability_object")
    },
    @{
        name = 'M32 the finished scan is adopted before it is validated'
        file = $orchestratorPath
        find = '        queries\.scan_counts\(result\)\r?\n'
        repl = "        result = result`n"
        tests = @($scannerWiring, $behaviour)
        select = @("-k", "autoquant_finish_rejects_a_wrong_type or unexpected_result_never_reaches_the_capability")
    },
    #: The three below reproduce the ownership blocker the PR review found: the
    #: route deciding, and commanding, a Market stop through a Paper fact and a
    #: provider seam.  M33 is the decision half, M34 the seam half, M35 the
    #: composition interlock being bypassed.
    @{
        name = 'M33 the route reads Paper obligations to decide a stop again'
        file = $orchestratorPath
        find = '        self\.market_stop_requested\.emit\(\)'
        repl = "        if self._paper.has_runtime_obligations:`n            self.information_requested.emit(STOP_STREAM_BLOCKED_TITLE, STOP_STREAM_BLOCKED_MESSAGE)`n            return`n        self.market_stop_requested.emit()"
        tests = @($g2bGuards, $behaviour)
        select = @("-k", "request_stop_stream_only_publishes_the_request or stop_request_is_only_a_request or stop_seam_is_gone_from_the_paper_port")
    },
    @{
        name = 'M34 a Market stop seam returns to the provider surface'
        file = $modelsPath
        find = '    probe_order_channel: Callable\[\[\], object\]'
        repl = "    probe_order_channel: Callable[[], object]`n    stop_market_data: Callable[[], bool]"
        tests = @($g2bGuards)
        select = @("-k", "provider_surface_is_frozen_and_command_free")
    },
    @{
        name = 'M35 the stop bridge skips the Paper interlock'
        file = $desktopPath
        find = '        if self\.paper_orchestrator\.has_runtime_obligations:\r?\n            self\.execution_orchestrator\.on_market_stop_refused\(\)\r?\n            return'
        repl = "        if False:`n            self.execution_orchestrator.on_market_stop_refused()`n            return"
        tests = @($behaviour, $g2bGuards)
        select = @("-k", "composition_refuses_the_stop_when_paper_has_obligations or refusal_copy_reaches_the_operator or execution_stop_bridge_owns_the_interlock")
    }
)

Write-Host "interpreter: $py" -ForegroundColor Cyan

$results = @()
foreach ($mutation in $mutations) {
    $path = $mutation.file
    $original = Get-Text $path
    $backup = "$path.mutation-backup"
    Copy-Item -LiteralPath $path -Destination $backup -Force
    $caught = $null
    $detail = ""
    try {
        # NOTE: the trailing ``1`` here binds to ``RegexOptions`` (1 ==
        # IgnoreCase), NOT to a replacement count -- the static
        # ``[regex]::Replace`` overloads have no count parameter.  Every
        # ``find`` pattern in this file must therefore match exactly one
        # location in its target (case-insensitively, which is how this
        # actually runs); a future edit that duplicates a pattern's shape would
        # mutate every copy.  Verify uniqueness when adding a mutant.
        $mutated = [regex]::Replace($original, $mutation.find, $mutation.repl, 1)
        if ($mutated -eq $original) {
            # A pattern that matches nothing is a hole in *this script*: the
            # mutation never happened, so the run says nothing about the guard.
            $caught = "HARNESS-ERROR"
            $detail = "the mutation applied nothing (pattern not found)"
        }
        else {
            Set-Text $path $mutated
            $syntax = & $py -c "import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())" $path 2>&1
            if ($LASTEXITCODE -ne 0) {
                $caught = "HARNESS-ERROR"
                $detail = "the mutation produced invalid syntax: $($syntax | Select-Object -Last 1)"
            }
            else {
                $arguments = @("-m", "pytest", "-q", "-p", "no:cacheprovider") + $mutation.tests + $mutation.select
                $output = & $py @arguments 2>&1
                $exit = $LASTEXITCODE
                if ($exit -eq 5) {
                    $caught = "HARNESS-ERROR"
                    $detail = "no tests were selected by $($mutation.select -join ' ')"
                }
                else {
                    if (-not ($output | Select-String -Pattern '\d+ (passed|failed)')) {
                        $caught = "HARNESS-ERROR"
                        $detail = "no test count in the output"
                    }
                    else {
                        $caught = ($exit -ne 0)
                    }
                    $detail = ($output | Select-String -Pattern '^\d+ (failed|passed)' | Select-Object -Last 1)
                    if (-not $detail) {
                        $detail = ($output | Select-Object -Last 1)
                    }
                }
            }
        }
    }
    catch {
        $caught = "ERROR"
        $detail = $_.Exception.Message
    }
    finally {
        Copy-Item -LiteralPath $backup -Destination $path -Force
        Remove-Item -LiteralPath $backup -Force
    }
    $results += [pscustomobject]@{
        Mutation = $mutation.name
        Caught = $caught
        Detail = "$detail"
    }
    Write-Host ("{0,-62} caught={1}  {2}" -f $mutation.name, $caught, $detail)
}

Write-Host ""
Write-Host "=== summary ==="
$results | Format-Table -AutoSize | Out-String | Write-Host
$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED: $($row.Mutation) -> $($row.Detail)" }

if ($uncaught.Count -gt 0) { exit 1 }
exit 0
