# Mutation verification for v2O-F1 (Runtime Events orchestration).
#
# Each entry breaks one property the round established, then asserts that the
# guard or behaviour test which names that property actually fails.  A mutation
# that survives is a hole: either the guard is not testing what it claims, or the
# property is not locked.
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
# The syntax gate is the important one: a `find`/`repl` pair that drops an
# indentation turns the module into a SyntaxError, pytest then reports a
# collection error, and the run says nothing at all about the property under
# test.  Every pattern therefore carries its own leading indentation, and every
# replacement repeats it verbatim.
#
# The script exits non-zero if any mutation survived or any harness error
# occurred, so it can be used as a gate rather than read as a report.
#
# Run:
#
#     .\scripts\mutation_system_runtime_events_f1.ps1

$ErrorActionPreference = "Stop"

# Repo-root anchored, so the script behaves the same from any working directory
# (the same rule ``verify.ps1`` and ``review.ps1`` follow).  Every path below is
# absolute for the same reason.
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

# An interpreter that cannot import the suite would report every mutation as a
# harness error.  Fail with the fix instead of producing misleading rows.
& $py -c "import pytest, PySide6" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "The interpreter '$py' cannot import pytest / PySide6; install the ``desktop`` and ``test`` extras into it, or set ``USQUANT_PYTHON`` to one that has them." -ForegroundColor Red
    exit 2
}

$orchestrator = Join-Path $projectRoot "src\us_quant\desktop_v2\orchestration\system\runtime_events\orchestrator.py"
$desktopPath = Join-Path $projectRoot "src\us_quant\desktop.py"

$behaviour = Join-Path $projectRoot "tests/test_desktop_runtime_events_orchestrator.py"
$architecture = Join-Path $projectRoot "tests/test_desktop_runtime_events_orchestration_architecture.py"
$timerSeam = Join-Path $projectRoot "tests/test_desktop_runtime_events_timer_seam.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText(
        $path, $text, (New-Object System.Text.UTF8Encoding($false))
    )
}

#: Line/indent-preserving edits.  ``find`` always starts at a line's own indentation and
#: ``repl`` always repeats it, so a mutation cannot change a block's nesting by accident.
$mutations = @(
    @{
        name = "M1  record does not write the store"
        file = $orchestrator
        find = "        event = self\._write\(\r?\n            severity=severity,\r?\n            component=component,\r?\n            code=code,\r?\n            message=message,\r?\n        \)\r?\n"
        repl = "        event = RuntimeEvent(`n            event_id=0,`n            occurred_at="""",`n            severity=severity,`n            component=component,`n            code=code,`n            message=message,`n            resolved=False,`n        )`n"
        tests = @($behaviour)
        select = @("-k", "record_writes_once")
    },
    @{
        name = "M2  record does not schedule a repaint"
        file = $orchestrator
        find = "        self\._schedule_refresh\(\)\r?\n        return event"
        repl = "        return event"
        tests = @($behaviour)
        select = @("-k", "record_writes_once")
    },
    @{
        name = "M3  refresh reuses a cached event list"
        file = $orchestrator
        find = "        environment = self\._environment\r?\n        return build_runtime_events_view\(\r?\n            events=self\._store\.list_recent\(RECENT_EVENT_LIMIT\),"
        repl = "        environment = self._environment`n        cached = getattr(self, ""_mutant_events_cache"", None)`n        if cached is None:`n            cached = self._store.list_recent(RECENT_EVENT_LIMIT)`n            self._mutant_events_cache = cached`n        return build_runtime_events_view(`n            events=cached,"
        tests = @($behaviour)
        select = @("-k", "every_paint_reads_the_store or flush_reads_the_store")
    },
    @{
        name = "M4  resolve(None) still calls store.resolve"
        file = $orchestrator
        find = "        if event_id is None:\r?\n            self\.information_requested\.emit\("
        repl = "        if event_id is None:`n            self._store.resolve(0)`n            self.information_requested.emit("
        tests = @($behaviour)
        select = @("-k", "resolve_none")
    },
    @{
        name = "M5  resolve(valid) does not repaint immediately"
        file = $orchestrator
        find = "        self\._store\.resolve\(event_id\)\r?\n        self\.refresh\(\)"
        repl = "        self._store.resolve(event_id)"
        tests = @($behaviour)
        select = @("-k", "resolve_uses_the_stable_id or resolve_does_not_wait")
    },
    @{
        name = "M6  a failed export still updates last_export"
        file = $orchestrator
        find = "        except \(OSError, ValueError\) as error:\r?\n            self\.warning_requested\.emit\(EXPORT_FAILED_TITLE, str\(error\)\)"
        repl = "        except (OSError, ValueError) as error:`n            self._last_export = (""failed"", str(error))`n            self.warning_requested.emit(EXPORT_FAILED_TITLE, str(error))"
        tests = @($behaviour)
        select = @("-k", "refused_export or later_failure_keeps")
    },
    @{
        name = "M7  a failed export still records EXPORT_OK"
        file = $orchestrator
        find = "        except \(OSError, ValueError\) as error:\r?\n            self\.warning_requested\.emit\(EXPORT_FAILED_TITLE, str\(error\)\)"
        repl = "        except (OSError, ValueError) as error:`n            self._write(`n                severity=""info"",`n                component=EXPORT_COMPONENT,`n                code=EXPORT_OK,`n                message=""failed"",`n            )`n            self.warning_requested.emit(EXPORT_FAILED_TITLE, str(error))"
        tests = @($behaviour)
        select = @("-k", "refused_export")
    },
    @{
        name = "M8  a successful export does not update last_export"
        file = $orchestrator
        find = "        self\._last_export = \(target\.name, str\(target\)\)\r?\n        self\._write\("
        repl = "        self._write("
        tests = @($behaviour)
        select = @("-k", "successful_export_sequences or later_failure_keeps")
    },
    @{
        name = "M9  active_task_count is captured at construction"
        file = $orchestrator
        find = "        self\._active_task_count = active_task_count"
        repl = "        _mutant_initial_tasks = active_task_count()`n        self._active_task_count = lambda: _mutant_initial_tasks"
        tests = @($behaviour)
        select = @("-k", "task_count_is_read_on_every_paint or task_count_burst")
    },
    @{
        name = "M10 coalescing arms one flush per arrival"
        file = $orchestrator
        find = "        if self\._refresh_pending:\r?\n            return\r?\n        self\._refresh_pending = True"
        repl = "        self._refresh_pending = True"
        tests = @($behaviour)
        select = @("-k", "burst_of_arrivals or task_count_burst")
    },
    @{
        name = "M11 the window writes the store itself again"
        file = $desktopPath
        find = "        self\.runtime_events_orchestrator\.record\(\r?\n            severity=event\.severity,\r?\n            component=event\.component,\r?\n            code=event\.code,\r?\n            message=event\.message,\r?\n        \)"
        repl = "        self.runtime_events_orchestrator._store.add(`n            severity=event.severity,`n            component=event.component,`n            code=event.code,`n            message=event.message,`n        )"
        tests = @($architecture)
        select = @("-k", "never_calls_a_store_method or routing_adapter_only_forwards")
    },
    @{
        name = "M12 the window owns last_export again"
        file = $desktopPath
        find = "        orchestrator\.export_succeeded\.connect\(\r?\n            self\._show_runtime_export_succeeded\r?\n        \)"
        repl = "        self._last_runtime_export = None`n        orchestrator.export_succeeded.connect(`n            self._show_runtime_export_succeeded`n        )"
        tests = @($architecture)
        select = @("-k", "no_runtime_events_sequencing_state")
    },

    # The two below were added after an independent review demonstrated that the
    # first one survived every test in the round: nothing drove the real Qt
    # timer, so a seam that stored its callback and never started the timer went
    # unnoticed.  ``test_desktop_runtime_events_timer_seam.py`` exists to kill it.

    @{
        name = "M13 the real timer stores the callback but never arms"
        file = $orchestrator
        find = "        self\._callback = callback\r?\n        self\._timer\.start\(round\(delay_seconds \* 1000\)\)"
        repl = "        self._callback = callback"
        tests = @($timerSeam)
        select = @("-k", "real_timer or coalesced_burst or task_count_change_is_repainted")
    },
    @{
        name = "M14 a late flush paints after it was superseded"
        file = $orchestrator
        find = "        if not self\._refresh_pending:\r?\n            return\r?\n        self\.refresh\(\)"
        repl = "        self.refresh()"
        tests = @($behaviour)
        select = @("-k", "superseded_flush")
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
            # Reported as HARNESS-ERROR rather than as a caught mutation.
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
                # Exit code 5 is "no tests collected", which is a hole in *this script*, not a
                # caught mutation: a selector that matches nothing would otherwise be reported
                # as a guard doing its job.
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
    Write-Host ("{0,-58} caught={1}  {2}" -f $mutation.name, $caught, $detail)
}

Write-Host ""
Write-Host "=== summary ==="
$results | Format-Table -AutoSize | Out-String | Write-Host
$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED: $($row.Mutation) -> $($row.Detail)" }

if ($uncaught.Count -gt 0) { exit 1 }
exit 0
