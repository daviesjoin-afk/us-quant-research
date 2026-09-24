# Mutation verification for v2O-E4.
#
# Each entry breaks one property the round established, then asserts that the guard or
# behaviour test which names that property actually fails.  A mutation that survives is a
# hole: either the guard is not testing what it claims, or the property is not locked.
#
# The harness deliberately distinguishes three outcomes and only `caught=True` counts as a
# caught mutation:
#
#   caught=True           the selected tests ran and failed -- RED, as required;
#   caught=HARNESS-ERROR  the regex matched nothing, the mutation produced invalid Python,
#                         pytest exited 5 ("no tests collected"), or the output had no
#                         "N passed|failed" line.  Any of those is a hole in *this* script,
#                         and reporting one as "caught" would let a broken mutation look
#                         like a working guard;
#   caught=ERROR          an unexpected exception while mutating or restoring.
#
# The syntax gate is the important one: a `find`/`repl` pair that drops an indentation
# turns the module into a SyntaxError, pytest then reports a collection error, and the run
# says nothing at all about the property under test.
#
# Every pattern therefore carries its own leading indentation, and every replacement
# repeats it verbatim.

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"
$py = ".\.venv\Scripts\python.exe"

$orchestrator = "src\us_quant\desktop_v2\orchestration\paper\orchestrator.py"
$desktopPath = "src\us_quant\desktop.py"
$projectorPath = "src\us_quant\desktop_v2\pages\execution\projector.py"

$closure = "tests/test_desktop_paper_presentation_closure.py"
$architecture = "tests/test_desktop_paper_orchestration_architecture.py"

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
        name = "M1  a finalized result is not retained"
        file = $orchestrator
        find = "        projection = paper_presentation\.project_presentation\(result\)\r?\n        if projection is None:\r?\n            return\r?\n"
        repl = "        if getattr(result.state, ""finalized"", False):`n            return`n        projection = paper_presentation.project_presentation(result)`n        if projection is None:`n            return`n"
        tests = @($closure)
        select = @("-k", "finalized_session_survives or route_still_renders_the_finished")
    },
    @{
        name = "M2  releasing the canonical result blanks the view"
        file = $orchestrator
        find = "        self\._retain_presentation\(result\)\r?\n        self\.result_changed\.emit\(result\)"
        repl = "        self._retain_presentation(result)`n        if getattr(result.state, ""finalized"", False):`n            self._presentation = None`n        self.result_changed.emit(result)"
        tests = @($closure)
        select = @("-k", "finalized_session_survives or exactly_one_writer or route_still_renders_the_finished")
    },
    @{
        name = "M3  the window caches the result's snapshot again"
        file = $desktopPath
        find = "        self\._render_auto_quant_snapshot\(\)\r?\n        self\._apply_paper_workflow_button_state\(\)"
        repl = "        self._paper_render_snapshot = result.engine_snapshot`n        self._render_auto_quant_snapshot()`n        self._apply_paper_workflow_button_state()"
        tests = @($architecture)
        select = @("-k", "keeps_no_paper_presentation_cache or presentation_cache_under_another_name")
    },
    @{
        name = "M4  the shutdown verdict reads the retained view"
        file = $orchestrator
        find = "        phase = self\._workflow\.phase\r?\n        if phase in \{PaperWorkflowPhase\.RUNNING, PaperWorkflowPhase\.PAUSED\}:"
        repl = "        if self._presentation is not None and not self._presentation.active:`n            return PaperShutdownResult(PaperShutdownDisposition.READY)`n        phase = self._workflow.phase`n        if phase in {PaperWorkflowPhase.RUNNING, PaperWorkflowPhase.PAUSED}:"
        tests = @($closure)
        select = @("-k", "no_decision_path_reads_the_retained_view or shutdown_verdict_on_canonical_truth")
    },
    @{
        name = "M5  the start gate reads the retained view"
        file = $orchestrator
        find = "        if queries\.launch_attempt_in_flight\(self\._workflow\.phase\):\r?\n            self\.refused\.emit\(DUPLICATE_TITLE, DUPLICATE_MESSAGE\)\r?\n            return"
        repl = "        if self._presentation is not None and not self._presentation.active:`n            self.refused.emit(DUPLICATE_TITLE, DUPLICATE_MESSAGE)`n            return`n        if queries.launch_attempt_in_flight(self._workflow.phase):`n            self.refused.emit(DUPLICATE_TITLE, DUPLICATE_MESSAGE)`n            return"
        tests = @($closure)
        select = @("-k", "no_decision_path_reads_the_retained_view or does_not_gate_a_new_launch")
    },
    @{
        name = "M6  the view is dropped outside an active session phase"
        file = $orchestrator
        find = "        return self\._presentation\r?\n\r?\n    @property\r?\n    def session_control_facts"
        repl = "        if not queries.active_session_phase(self._workflow.phase):`n            return None`n        return self._presentation`n`n    @property`n    def session_control_facts"
        tests = @($closure)
        select = @("-k", "survives_preparing or failed_connect_leaves or finalized_session_survives")
    },
    @{
        name = "M7  a new session does not replace the previous view"
        file = $orchestrator
        find = "        if projection is None:\r?\n            return\r?\n        self\._presentation = projection"
        repl = "        if projection is None:`n            return`n        if self._presentation is None:`n            self._presentation = projection"
        tests = @($closure)
        select = @("-k", "replaces_the_previous_one or replaces_the_retained_view")
    },
    @{
        name = "M8  the route reads the canonical result instead of the view"
        file = $desktopPath
        find = "        session = self\.paper_orchestrator\.presentation\r?\n        if session is None:\r?\n            return"
        repl = "        result = self.paper_workflow.result`n        session = result.engine_snapshot if result is not None else None`n        if session is None:`n            return"
        tests = @($architecture, $closure)
        select = @("-k", "route_draws_the_capabilitys_retained_presentation or renders_the_execution_route_once")
    },
    @{
        name = "M9  the projector mutates the broker it was handed"
        file = $projectorPath
        find = "    session_id = str\(getattr\(session, ""session_id"", """"\) or """"\)\r?\n    return build_runtime_view\("
        repl = "    session_id = str(getattr(session, ""session_id"", """") or """")`n    if broker_state is not None:`n        broker_state.disconnect()`n    return build_runtime_view("
        tests = @($closure)
        select = @("-k", "read_model_is_pure or projector_never_mutates")
    },
    @{
        name = "M10 the window assembles the session view again"
        file = $desktopPath
        find = "            build_session_view\(\r?\n                session=session,"
        repl = "            build_runtime_view(`n                snapshot=session,"
        tests = @($architecture)
        select = @("-k", "fetches_and_delegates_rather_than_assembling")
    },
    @{
        name = "M11 the view is retained after the result is published"
        file = $orchestrator
        find = "        self\._retain_presentation\(result\)\r?\n        self\.result_changed\.emit\(result\)"
        repl = "        self.result_changed.emit(result)`n        self._retain_presentation(result)"
        tests = @($architecture)
        select = @("-k", "retained_before_the_result_is_published")
    }
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
        $mutated = [regex]::Replace($original, $mutation.find, $mutation.repl, 1)
        if ($mutated -eq $original) {
            throw "mutation applied nothing (pattern not found): $($mutation.name)"
        }
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
Write-Host "mutations not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED: $($row.Mutation) -> $($row.Detail)" }
