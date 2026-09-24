# Mutation verification for v2O-E3.
#
# Each entry breaks one property the round established, then asserts that the guard or
# behaviour test which names that property actually fails.  A mutation that survives is
# a hole: either the guard is not testing what it claims, or the property is not locked.
#
# The harness deliberately distinguishes three outcomes and only `caught=True` counts as
# a caught mutation:
#
#   caught=True         the selected tests ran and failed -- RED, as required;
#   caught=HARNESS-ERROR  the regex matched nothing, the mutation produced invalid Python,
#                         pytest exited 5 ("no tests collected"), or the output had no
#                         "N passed|failed" line.  Any of those is a hole in *this
#                         script*, and reporting one as "caught" would let a broken
#                         mutation look like a working guard;
#   caught=ERROR        an unexpected exception while mutating or restoring.
#
# The syntax gate is the important one: a `find`/`repl` pair that drops an indentation
# turns the module into a SyntaxError, pytest then reports a collection error, and the
# run says nothing at all about the property under test.
#
# Every pattern therefore carries its own leading indentation, and every replacement
# repeats it verbatim.

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"
$py = ".\.venv\Scripts\python.exe"

$orchestrator = "src\us_quant\desktop_v2\orchestration\paper\orchestrator.py"
$queriesPath = "src\us_quant\desktop_v2\orchestration\paper\queries.py"
$activeRelease = "src\us_quant\trading\application\paper\active_release.py"
$desktopPath = "src\us_quant\desktop.py"

$behavior = "tests/test_desktop_paper_recovery_finalization_orchestrator.py"
$architecture = "tests/test_desktop_paper_orchestration_architecture.py"
$paperWiring = "tests/test_desktop_v2_paper_wiring.py"
$serviceTests = "tests/test_paper_trading_service.py"
$teardown = "tests/test_desktop_runtime_teardown.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText(
        $path, $text, (New-Object System.Text.UTF8Encoding($false))
    )
}

#: Line/indent-preserving edits.  ``find`` always starts at a line's own indentation and
#: ``repl`` always repeats it, so a mutation cannot change a block's nesting by accident.
$mutations = @(
    # -- the finalization gate -------------------------------------------
    @{
        name = "M1  the in-flight proof gate is removed"
        file = $orchestrator
        find = "        if self\._finalization_inflight:\r?\n            return\r?\n        engine_active"
        repl = "        engine_active"
        tests = @($behavior)
        select = @("-k", "already_in_flight or proof_ends")
    },
    @{
        name = "M2  the 5s backoff is removed"
        file = $orchestrator
        find = "        if \(\r?\n            engine_active\r?\n            and last_started is not None\r?\n            and self\._clock\(\) - last_started < FINALIZATION_REFRESH_BACKOFF_SECONDS\r?\n        \):\r?\n            return\r?\n"
        repl = ""
        tests = @($behavior)
        select = @("-k", "backoff")
    },
    @{
        name = "M3  the backoff no longer waits for the engine to stop"
        file = $orchestrator
        find = "engine_active\r?\n            and last_started is not None"
        repl = "last_started is not None"
        tests = @($behavior)
        select = @("-k", "dormant_engine_is_not_held_back")
    },
    @{
        name = "M4  the STOPPING-only rule is relaxed"
        file = $orchestrator
        find = "        if self\._workflow\.phase is not PaperWorkflowPhase\.STOPPING:\r?\n            return\r?\n"
        repl = ""
        tests = @($behavior)
        select = @("-k", "only_stopping_schedules")
    },
    @{
        name = "M5  a finalized result schedules the proof again"
        file = $orchestrator
        find = "        if result\.state\.finalized:\r?\n            return\r?\n        if self\._finalization_inflight"
        repl = "        if self._finalization_inflight"
        tests = @($behavior)
        select = @("-k", "finalized_result_schedules_nothing")
    },
    @{
        name = "M6  a busy broker group is reported as a finalization failure"
        file = $orchestrator
        find = "            self\._finalization_inflight = False\r?\n            self\.presentation_refresh_requested\.emit\(\)"
        repl = "            self._workflow.fail_finalization_refresh()`n            self._finalization_inflight = False`n            self.presentation_refresh_requested.emit()"
        tests = @($behavior)
        select = @("-k", "busy_broker_group_defers")
    },

    # -- the proof's fixed order -----------------------------------------
    @{
        name = "M7  the disconnect runs before the evidence is captured"
        file = $orchestrator
        find = "            result, evidence_id = self\._workflow\.capture_finalization_evidence\(\)\r?\n            if evidence_id is None:\r?\n                return result\r?\n            self\._paper_trading\.disconnect\(\)"
        repl = "            self._paper_trading.disconnect()`n            result, evidence_id = self._workflow.capture_finalization_evidence()`n            if evidence_id is None:`n                return result"
        tests = @($behavior)
        select = @("-k", "captures_evidence_then_disconnects")
    },
    @{
        name = "M8  the confirmation runs before the disconnect"
        file = $orchestrator
        find = "            self\._paper_trading\.disconnect\(\)\r?\n            return self\._workflow\.confirm_finalization_after_disconnect\(evidence_id\)"
        repl = "            confirmed = self._workflow.confirm_finalization_after_disconnect(evidence_id)`n            self._paper_trading.disconnect()`n            return confirmed"
        tests = @($behavior)
        select = @("-k", "captures_evidence_then_disconnects or disconnect_failure_releases_nothing")
    },

    # -- releasing the session -------------------------------------------
    @{
        name = "M9  the release helper trusts its caller about finalization"
        file = $orchestrator
        find = "        if not result\.state\.finalized:\r?\n            return ""the session does not report itself finalized""\r?\n"
        repl = ""
        tests = @($behavior)
        select = @("-k", "refuses_an_unfinalized_result_of_its_own_accord")
    },
    @{
        name = "M10 broker positions no longer stop the release"
        file = $orchestrator
        find = "        if getattr\(broker_state, ""positions"", \(\)\):\r?\n            return ""the broker still reports positions""\r?\n"
        repl = ""
        tests = @($behavior)
        select = @("-k", "broker_position_stops_the_release or shutdown_blocks_when_the_broker")
    },
    @{
        name = "M11 unreconciled rows no longer stop the release"
        file = $orchestrator
        find = "        if any\(\r?\n            not getattr\(row, ""reconciled"", False\)\r?\n            for row in self\._reconciliation_rows_provider\(str\(session_id\)\)\r?\n        \):\r?\n            return ""the order journal still has unreconciled rows""\r?\n"
        repl = ""
        tests = @($behavior)
        select = @("-k", "unreconciled_row_stops_the_release or shutdown_blocks_when_the_journal")
    },
    @{
        name = "M12 the workflow's own release gate is ignored"
        file = $orchestrator
        find = "            if not self\._paper_trading\.cancel_active_release\(reservation\):\r?\n                return ""the reserved release could not be given back""\r?\n            return ""the workflow refused to release the Paper lease"""
        repl = "            self._paper_trading.commit_active_release(reservation)`n            return ""the workflow refused to release the Paper lease"""
        tests = @($behavior)
        select = @("-k", "workflow_refusal_gives_the_reservation_back")
    },
    @{
        name = "M13 a failed finalization no longer halts the session"
        file = $orchestrator
        find = "        self\._finalization_inflight = False\r?\n        self\._workflow\.fail_finalization_refresh\(\)\r?\n"
        repl = "        self._finalization_inflight = False`n"
        tests = @($behavior)
        select = @("-k", "proof_that_really_fails or proof_task_that_raises")
    },

    # -- manual recovery --------------------------------------------------
    @{
        name = "M14 reconciliation resumes the session by itself"
        file = $orchestrator
        find = "            return self\._workflow\.complete_manual_reconciliation\(attempt_id\)"
        repl = "            self._workflow.complete_manual_reconciliation(attempt_id)`n            return self._workflow.confirm_manual_resume(`n                self._workflow.reconciliation_evidence.evidence_id`n            )"
        tests = @($behavior)
        select = @("-k", "collects_evidence or publishes_its_result_once")
    },
    @{
        name = "M15 the resume stops checking for a fresh proof"
        file = $queriesPath
        find = "    return phase is PaperWorkflowPhase\.RECONCILING_READY and evidence is not None"
        repl = "    return True"
        tests = @($behavior, $paperWiring)
        select = @("-k", "without_a_fresh_proof or without_a_proof or no_current_proof")
    },
    @{
        name = "M16 a failed reconciliation no longer fails its attempt"
        file = $orchestrator
        find = "        self\._workflow\.fail_manual_reconciliation\(attempt_id\)\r?\n        self\.log_requested\.emit\(message\)"
        repl = "        self.log_requested.emit(message)"
        tests = @($behavior)
        select = @("-k", "task_that_fails_returns_to_sticky_halted")
    },
    @{
        name = "M17 a refused reconciliation task leaves the phase reconciling"
        file = $orchestrator
        find = "            self\._workflow\.fail_manual_reconciliation\(attempt_id\)\r?\n            self\.presentation_refresh_requested\.emit\(\)\r?\n            self\._announce_manual_recovery_if_required\(\)"
        repl = "            self.presentation_refresh_requested.emit()"
        tests = @($behavior)
        select = @("-k", "never_admitted_rolls_its_attempt_back")
    },
    @{
        name = "M18 the confirmation reads the proof inside the task instead of freezing it"
        file = $orchestrator
        find = "            return self\._workflow\.confirm_manual_resume\(evidence_id\)"
        repl = "            return self._workflow.confirm_manual_resume(`n                self._workflow.reconciliation_evidence.evidence_id`n            )"
        tests = @($behavior)
        select = @("-k", "superseded_proof or consumed_proof")
    },

    # -- the shutdown verdict ---------------------------------------------
    @{
        name = "M19 prepare_shutdown treats HALTED as an automatic route"
        file = $orchestrator
        find = "                PaperShutdownDisposition\.MANUAL_RECOVERY_REQUIRED,\r?\n                SHUTDOWN_MANUAL_RECOVERY_MESSAGE,"
        repl = "                PaperShutdownDisposition.WAITING_FOR_FINALIZATION,`n                SHUTDOWN_MANUAL_RECOVERY_MESSAGE,"
        tests = @($behavior)
        select = @("-k", "operator_only_phase_requires_the_operator")
    },
    @{
        name = "M20 prepare_shutdown disconnects a STOPPING session"
        file = $orchestrator
        find = "        if phase is PaperWorkflowPhase\.STOPPING:\r?\n            return PaperShutdownResult\("
        repl = "        if phase is PaperWorkflowPhase.STOPPING:`n            self._paper_trading.disconnect()`n            return PaperShutdownResult("
        tests = @($behavior)
        select = @("-k", "stopping_session_does_not_disconnect")
    },
    @{
        name = "M21 an owned slot with no session to prove anything about is READY"
        file = $orchestrator
        find = "        if result is None:\r?\n(?:            #[^\r\n]*\r?\n)*            return self\._ownership_blocked\(SHUTDOWN_UNPROVABLE_SESSION_REASON\)\r?\n"
        repl = "        if result is None:`n            return PaperShutdownResult(PaperShutdownDisposition.READY)`n"
        tests = @($behavior)
        select = @("-k", "owned_slot_with_no_session_at_all or promotion_claim_with_no_result_at_all")
    },
    @{
        name = "M22 every blocked ownership is reported as READY"
        file = $orchestrator
        find = "        return PaperShutdownResult\(\r?\n            PaperShutdownDisposition\.OWNERSHIP_BLOCKED,\r?\n            SHUTDOWN_OWNERSHIP_BLOCKED_WITH_REASON_MESSAGE\.format\(reason=reason\),\r?\n        \)"
        repl = "        return PaperShutdownResult(`n            PaperShutdownDisposition.READY,`n            SHUTDOWN_OWNERSHIP_BLOCKED_WITH_REASON_MESSAGE.format(reason=reason),`n        )"
        tests = @($behavior)
        select = @("-k", "ownership_cannot_be_released or broker_still_reports_positions or journal_still_has or orderly_stop_was_refused")
    },
    @{
        name = "M23 READY is returned without the slot having been given up"
        file = $orchestrator
        find = "        if not self\._paper_trading\.has_order_service\(\):\r?\n(?:            #[^\r\n]*\r?\n)*            return PaperShutdownResult\(PaperShutdownDisposition\.READY\)\r?\n"
        repl = "        return PaperShutdownResult(PaperShutdownDisposition.READY)`n"
        tests = @($behavior, $architecture)
        select = @("-k", "shutdown_blocks or ownership_block_is_never_downgraded")
    },
    @{
        name = "M24 prepare_shutdown stops a running session twice"
        file = $orchestrator
        find = "            self\.stop\(\)\r?\n            return self\._shutdown_verdict_after_the_stop\(\)"
        repl = "            self.stop()`n            self.stop()`n            return self._shutdown_verdict_after_the_stop()"
        tests = @($behavior)
        select = @("-k", "reuses_the_capabilitys_own_stop or stops_a_running_session_exactly_once")
    },

    # -- the capability's boundary ---------------------------------------
    @{
        name = "M25 the manual-recovery announcement stops firing"
        file = $orchestrator
        find = "        self\.manual_recovery_required\.emit\(\)"
        repl = "        pass"
        tests = @($behavior, $teardown)
        select = @("-k", "manual_recovery or recovery_publication or recovery_required")
    },
    @{
        name = "M26 the window drives the workflow's release gate again"
        file = $desktopPath
        find = "        self\._publish_execution_controls\(\)\r?\n\r?\n    def _render_auto_quant_snapshot"
        repl = "        self._publish_execution_controls()`n        self.paper_workflow.finalize_if_safe()`n`n    def _render_auto_quant_snapshot"
        tests = @($architecture)
        select = @("-k", "never_drives_recovery_or_finalization")
    },
    @{
        name = "M27 the window keeps the proof flag again"
        file = $desktopPath
        find = "        self\._last_runtime_events_refresh = 0\.0"
        repl = "        self._last_runtime_events_refresh = 0.0`n        self._paper_finalization_inflight = False"
        tests = @($architecture)
        select = @("-k", "keeps_no_finalization_state or finalization_seam_is_gone")
    },
    @{
        name = "M28 the reconcile intent is routed back through the window"
        file = $desktopPath
        find = "page\.reconcile_requested\.connect\(self\.paper_orchestrator\.reconcile\)"
        repl = "page.reconcile_requested.connect(self._publish_execution_controls)"
        tests = @($architecture)
        select = @("-k", "session_intents_reach_the_capability")
    },
    @{
        name = "M29 the recovery result is published outside the one path"
        file = $orchestrator
        find = "        self\._publish_result\(result\)  # type: ignore\[arg-type\]\r?\n\r?\n    def _reconciliation_failed"
        repl = "        self.result_changed.emit(result)  # type: ignore[arg-type]`n`n    def _reconciliation_failed"
        tests = @($behavior, $architecture)
        select = @("-k", "one_result_path or publishes_its_result_once or is_the_only_emitter")
    },

    # -- the release's transaction boundary, and the shutdown classification --
    #
    # These five are the review's blockers, pinned as mutations rather than only as tests:
    # each one is a plausible edit that reintroduces a state the round exists to make
    # unreachable, and each has to be RED for the guard to be worth anything.

    @{
        name = "M30 the slot is reserved even while a promotion claim holds it"
        file = $activeRelease
        find = "            if self\._promotion_reservation is not None:\r?\n                raise PaperTradingLifecycleError\(\r?\n                    f""Paper candidate \{self\._promotion_reservation\.candidate_id!r\} holds""\r?\n                    "" the promotion reservation; refusing to reserve the slot it is""\r?\n                    "" reserved to""\r?\n                \)\r?\n"
        repl = ""
        tests = @($serviceTests, $behavior)
        select = @("-k", "promotion_holds_the_slot or ownership_cannot_be_released")
    },
    @{
        name = "M31 a reserved release no longer locks the other transitions"
        file = $activeRelease
        find = "        if self\._active_release_reservation is None:\r?\n            return\r?\n        raise PaperTradingLifecycleError\(\r?\n            f""an active Paper release is in flight; refusing to \{action\}""\r?\n        \)"
        repl = "        return"
        tests = @($serviceTests)
        select = @("-k", "locks_every_other_slot_transition or refuses_a_promotion_into_the_slot")
    },
    @{
        name = "M32 a reserved release cannot be given back"
        file = $activeRelease
        find = "            if self\._active_release_reservation is not reservation:\r?\n                return False\r?\n            self\._active_release_reservation = None\r?\n        return True"
        repl = "            return False"
        tests = @($serviceTests)
        select = @("-k", "gives_the_lock_back_and_drops_nothing")
    },
    @{
        name = "M33 CONNECTING is classified from 'the workflow holds no result'"
        file = $orchestrator
        find = "        if phase is PaperWorkflowPhase\.CONNECTING:\r?\n(?:            #[^\r\n]*\r?\n)*            return self\._ownership_blocked\(SHUTDOWN_LAUNCH_IN_FLIGHT_REASON\)\r?\n"
        repl = ""
        tests = @($behavior)
        select = @("-k", "connecting_attempt")
    },
    @{
        name = "M34 the shutdown verdict after a stop is hard-coded to waiting"
        file = $orchestrator
        find = "            self\.stop\(\)\r?\n            return self\._shutdown_verdict_after_the_stop\(\)"
        repl = "            self.stop()`n            return PaperShutdownResult(`n                PaperShutdownDisposition.WAITING_FOR_FINALIZATION,`n                SHUTDOWN_STOP_REQUESTED_MESSAGE,`n            )"
        tests = @($behavior)
        select = @("-k", "halt_an_orderly_stop_produced or fast_finalizing_stop or orderly_stop_was_refused")
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
