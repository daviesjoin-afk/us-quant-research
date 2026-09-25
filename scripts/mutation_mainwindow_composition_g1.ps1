# Mutation verification for G1 (MainWindow composition closure).
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
# Anchors are text lines, which is exactly how the two earlier rounds lost
# mutants silently (E3's M27 twice, E2's M4/M9): any round that edits
# ``desktop.py`` must re-run every harness in this directory.  A pattern that
# matches nothing is reported as HARNESS-ERROR and fails the script, so a dead
# anchor can never masquerade as a green gate.
#
# Run:
#
#     .\scripts\mutation_mainwindow_composition_g1.ps1

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
$tasksPath = Join-Path $projectRoot "src\us_quant\desktop_tasks.py"
$supervisorPath = Join-Path $projectRoot "src\us_quant\runtime_supervisor.py"

$guards = Join-Path $projectRoot "tests/test_desktop_composition_closure_architecture.py"
$teardown = Join-Path $projectRoot "tests/test_desktop_runtime_teardown.py"
$tasks = Join-Path $projectRoot "tests/test_desktop_tasks.py"
$f1 = Join-Path $projectRoot "tests/test_desktop_runtime_events_orchestration_architecture.py"
$dashboard = Join-Path $projectRoot "tests/test_desktop_dashboard_orchestrator.py"
$timerSeam = Join-Path $projectRoot "tests/test_desktop_runtime_events_timer_seam.py"
$wiring = Join-Path $projectRoot "tests/test_desktop_v2_dashboard_wiring.py"

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
        name = "M1  the window keeps a worker list alias again"
        file = $desktopPath
        find = "        self\.task_controller = DesktopTaskController\[TaskThread\]\(\)"
        repl = "        self.task_controller = DesktopTaskController[TaskThread]()`n        self.workers = self.task_controller.workers"
        tests = @($guards, $tasks)
        select = @("-k", "worker_collection_alias or worker_collection_is_only_mutated")
    },
    @{
        name = "M2  the runtime events task count is frozen at zero"
        file = $desktopPath
        find = "active_task_count=lambda: self\.task_controller\.active_count,"
        repl = "active_task_count=lambda: 0,"
        tests = @($f1, $timerSeam)
        select = @("-k", "composes_the_orchestrators_dependencies or task_count_change_is_repainted")
    },
    @{
        name = "M3  the admission gate never engages"
        file = $desktopPath
        find = "if self\.runtime_supervisor\.shutting_down and not shutdown_essential:"
        repl = "if getattr(self, ""_mutant_closing"", False) and not shutdown_essential:"
        tests = @($teardown, $guards)
        select = @("-k", "admission_gate_refuses_new_tasks or non_essential_task_is_still_refused or start_task_reads_the_supervisors")
    },
    @{
        name = "M4  the shutdown-essential exemption is inverted"
        file = $desktopPath
        find = "if self\.runtime_supervisor\.shutting_down and not shutdown_essential:"
        repl = "if self.runtime_supervisor.shutting_down and shutdown_essential:"
        tests = @($teardown)
        select = @("-k", "paper_session_without_raising_the_gate")
    },
    @{
        name = "M5  a refused close never lowers the admission gate"
        file = $desktopPath
        find = "self\.runtime_supervisor\.cancel_shutdown\(\)"
        repl = "pass"
        tests = @($teardown)
        select = @("-k", "manual_reconciliation or recovery_publication")
    },
    @{
        name = "M6  every refused disposition reopens the admission gate"
        file = $desktopPath
        find = "paper_shutdown\.disposition\r?\n                is PaperShutdownDisposition\.MANUAL_RECOVERY_REQUIRED"
        repl = "paper_shutdown.disposition`n                is not PaperShutdownDisposition.READY"
        tests = @($teardown)
        select = @("-k", "waiting_for_finalization_keeps or ownership_blocked_is_refused")
    },
    @{
        name = "M7  the Shadow shutdown is dropped from the close path"
        file = $desktopPath
        find = "        self\.shadow_orchestrator\.shutdown\(\)"
        repl = "        pass"
        tests = @($teardown)
        select = @("-k", "shuts_shadow_down_before_the_runtime")
    },
    @{
        name = "M8  the window exits over a live market worker"
        file = $desktopPath
        find = "if self\.market_orchestrator\.worker_running:"
        repl = "if False:"
        tests = @($teardown)
        select = @("-k", "stuck_market_worker or failed_release_is_reported")
    },
    @{
        name = "M9  a failed component stops the remaining releases"
        file = $supervisorPath
        # ``tolerate=True`` is what makes one component's failure isolated: the
        # failure is recorded and every later component is still released.  The
        # False-join path is the one the desktop's market stream actually takes
        # (``QThread.wait`` semantics), so the wiring test below is the killer.
        find = "self\._release\(component, tolerate=True\)"
        repl = "self._release(component, tolerate=False)"
        tests = @($teardown)
        select = @("-k", "failed_release_is_reported")
    },
    @{
        name = "M10 a runtime teardown failure writes no runtime event"
        file = $desktopPath
        find = 'code="RUNTIME_SHUTDOWN_PARTIAL",'
        repl = 'code="RUNTIME_SHUTDOWN_MUTANT",'
        tests = @($teardown)
        select = @("-k", "failed_release_is_reported")
    },
    @{
        name = "M11 the close path reads the Paper phase directly"
        file = $desktopPath
        find = "        paper_shutdown = self\.paper_orchestrator\.prepare_shutdown\(\)"
        repl = "        _mutant_phase = self.paper_workflow.phase`n        paper_shutdown = self.paper_orchestrator.prepare_shutdown()"
        tests = @($guards)
        select = @("-k", "close_event_reads_no_paper_internal")
    },
    @{
        name = "M12 the gateway result is stored as a second fact"
        file = $desktopPath
        find = "        result = probe_ibkr_socket\(self\.config\.ibkr\)"
        repl = "        result = probe_ibkr_socket(self.config.ibkr)`n        self._gateway_reachable = result.reachable"
        tests = @($guards)
        select = @("-k", "gateway_probe_is_a_pure_shell_diagnostic")
    },
    @{
        name = "M13 a GatewayOrchestrator is created"
        file = $desktopPath
        find = "def main\(\) -> int:"
        repl = "class GatewayOrchestrator:`n    pass`n`n`ndef main() -> int:"
        tests = @($guards)
        select = @("-k", "gateway_orchestrator_exists or god_object_was_created")
    },
    @{
        name = "M14 the window renders the dashboard page directly again"
        file = $desktopPath
        find = "        self\.dashboard_orchestrator\.set_chart\(chart\)"
        repl = "        self.dashboard_orchestrator.set_chart(chart)`n        self.dashboard_page.render(self.dashboard_orchestrator.build_view())"
        tests = @($guards)
        select = @("-k", "window_never_renders_the_dashboard_page or dashboard_render_caller_is_the_capability")
    },
    @{
        name = "M15 the dashboard owner freezes the market snapshot"
        file = $desktopPath
        find = "snapshot=lambda: self\.market_orchestrator\.snapshot,"
        repl = "snapshot=lambda _mutant=self.market_orchestrator.snapshot: _mutant,"
        tests = @($dashboard, $wiring)
        select = @("-k", "reads_every_provider_every_time or cached_between_repaints or market_snapshot_states_are_visible")
    },
    @{
        name = "M16 a bridge reads another capability's private member"
        file = $desktopPath
        find = "        self\.shadow_orchestrator\.shutdown\(\)"
        repl = "        self.shadow_orchestrator.shutdown()`n        _mutant_pending = self.market_orchestrator._stop_pending"
        tests = @($guards)
        select = @("-k", "reach_no_private_member")
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
