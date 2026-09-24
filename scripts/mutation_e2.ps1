# Mutation verification for v2O-E2.
#
# Each entry breaks one property the round established, then asserts that the guard or
# behaviour test which names that property actually fails.  A mutation that survives is
# a hole: either the guard is not testing what it claims, or the property is not locked.

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"
$py = ".\.venv\Scripts\python.exe"

$orchestrator = "src\us_quant\desktop_v2\orchestration\paper\orchestrator.py"
$queriesPath = "src\us_quant\desktop_v2\orchestration\paper\queries.py"
$desktopPath = "src\us_quant\desktop.py"

$behavior = "tests/test_desktop_paper_active_runtime_orchestrator.py"
$architecture = "tests/test_desktop_paper_orchestration_architecture.py"
$marketArch = "tests/test_desktop_market_orchestration_architecture.py"
$marketWiring = "tests/test_desktop_v2_market_wiring.py"
$paperWiring = "tests/test_desktop_v2_paper_wiring.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText(
        $path, $text, (New-Object System.Text.UTF8Encoding($false))
    )
}

$mutations = @(
    @{
        name = "M1  ingress runs whatever the phase says"
        file = $orchestrator
        find = 'if not queries\.active_session_phase\(self\._workflow\.phase\):\s+return\s+self\._last_stream_ingress_monotonic = self\._clock\(\)'
        repl = 'self._last_stream_ingress_monotonic = self._clock()'
        tests = @($behavior)
        select = @()
    },
    @{
        name = "M2  HALTED is treated as a live phase"
        file = $queriesPath
        find = 'PaperWorkflowPhase\.STOPPING,'
        repl = "PaperWorkflowPhase.STOPPING,`n        PaperWorkflowPhase.HALTED,"
        tests = @($behavior)
        select = @()
    },
    @{
        name = "M3  the suppression boundary becomes inclusive"
        file = $orchestrator
        find = 'self\._clock\(\) - last_ingress < STREAM_INGRESS_SUPPRESSION_SECONDS'
        repl = 'self._clock() - last_ingress <= STREAM_INGRESS_SUPPRESSION_SECONDS'
        tests = @($behavior)
        select = @("-k", "suppress")
    },
    @{
        name = "M4  the poll ignores the finalization seam"
        file = $orchestrator
        find = 'if self\._finalization_inflight_provider\(\):\s+return\s+last_ingress = '
        repl = 'last_ingress = '
        tests = @($behavior)
        select = @("-k", "finalization")
    },
    @{
        name = "M5  the poll emits the signal itself"
        file = $orchestrator
        find = 'self\._publish_result\(self\._workflow\.poll\(\)\)'
        repl = 'self.result_changed.emit(self._workflow.poll())'
        tests = @($architecture, $behavior)
        select = @("-k", "one_result_path or exactly_once")
    },
    @{
        name = "M6  the stop freezes the market fact at construction"
        file = $orchestrator
        find = 'self\._market_snapshot_provider = market_snapshot_provider'
        repl = "self._market_snapshot_provider = market_snapshot_provider`n        self._frozen_market = market_snapshot_provider()"
        tests = @($behavior, $architecture)
        select = @("-k", "latest_snapshot or injected_collaborators")
    },
    @{
        name = "M6b the frozen snapshot is the one the stop uses"
        file = $orchestrator
        find = 'self\._workflow\.request_stop\(self\._market_snapshot_provider\(\)\)'
        repl = 'self._workflow.request_stop(self._frozen_market)'
        tests = @($behavior)
        select = @("-k", "latest_snapshot")
    },
    @{
        name = "M7  the ingress no longer stamps the liveness moment"
        file = $orchestrator
        find = 'self\._last_stream_ingress_monotonic = self\._clock\(\)'
        repl = 'pass'
        tests = @($behavior)
        select = @("-k", "suppress")
    },
    @{
        name = "M8  a refused pause still reports success"
        file = $orchestrator
        find = 'except WorkflowStateError as error:\s+self\.log_requested\.emit\(str\(error\)\)\s+return\s+self\._publish_result\(result\)\s+self\.log_requested\.emit\(PAUSE_SUCCEEDED_MESSAGE\)'
        repl = "except WorkflowStateError as error:`n            self.log_requested.emit(PAUSE_SUCCEEDED_MESSAGE)`n            return`n        self._publish_result(result)`n        self.log_requested.emit(PAUSE_SUCCEEDED_MESSAGE)"
        tests = @($behavior)
        select = @("-k", "refused_outside_a_live_session")
    },
    @{
        name = "M9  the window keeps a runtime handle again"
        file = $desktopPath
        find = 'self\._paper_render_snapshot: AutoQuantSnapshot \| None = None'
        repl = "self._paper_render_snapshot: AutoQuantSnapshot | None = None`n        self.trading_runtime = None"
        tests = @($architecture)
        select = @("-k", "keeps_no_active_paper_state")
    },
    @{
        name = "M10 the heartbeat no longer reaches the capability"
        file = $desktopPath
        find = 'self\.paper_order_timer\.timeout\.connect\(\s*self\.paper_orchestrator\.poll\s*\)'
        repl = 'self.paper_order_timer.timeout.connect(lambda: None)'
        tests = @($architecture, $paperWiring)
        select = @("-k", "watchdog_heartbeat or heartbeat_reaches_the_capability")
    },
    @{
        name = "M11 the market fan-out drops the Paper fact"
        file = $desktopPath
        find = 'self\.paper_orchestrator\.on_market_snapshot\(snapshot\)'
        repl = 'pass'
        tests = @($marketArch, $marketWiring)
        select = @("-k", "declared_consumer or reaches_a_running_paper_session")
    },
    @{
        name = "M12 the obligation interlock answers False"
        file = $orchestrator
        find = 'return queries\.runtime_obligations\(\s+result\.engine_snapshot if result is not None else None\s+\)'
        repl = 'return False'
        tests = @($marketWiring)
        select = @("-k", "refuses_a_normal_stop or switch_is_refused")
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
            $caught = ($exit -ne 0)
            if (-not ($output | Select-String -Pattern '\d+ (passed|failed)')) {
                $caught = "HARNESS-ERROR"
                $detail = "no test count in the output"
            }
            $detail = ($output | Select-String -Pattern '^\d+ (failed|passed)' | Select-Object -Last 1)
            if (-not $detail) {
                $detail = ($output | Select-Object -Last 1)
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
    Write-Host ("{0,-60} caught={1}  {2}" -f $mutation.name, $caught, $detail)
}

Write-Host ""
Write-Host "=== summary ==="
$results | Format-Table -AutoSize | Out-String | Write-Host
$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations not caught: $($uncaught.Count)"
foreach ($row in $uncaught) { Write-Host "  SURVIVED: $($row.Mutation) -> $($row.Detail)" }
