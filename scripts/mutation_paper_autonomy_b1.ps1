# Mutation verification for the Paper autonomy v1-B foundation.
#
# Each entry breaks exactly one property the control core has to have, then
# asserts that the guard or behaviour test naming that property actually fails.
# A mutation that survives is a hole: either the guard is not testing what it
# claims, or the property is not locked.
#
# What this round is about is the *control boundary*: which session a supervisor
# may touch, when the clock outranks the operator, and which transitions an
# action record can take.  So the mutants move the provenance dispatch, drop the
# stop boundary, loosen the action state machine and make the store lenient about
# what it can read.
#
# Two mutants from the review's list are deliberately NOT here, and the reasons
# matter more than the count:
#
#   * "claim accepts a terminal initial state" has no anchor separate from
#     "claim accepts any state" -- both flow through the one status guard, and a
#     second mutant on the same line would be a duplicate wearing a new name.
#     Both cases are covered as parameters of the one test instead.
#   * "premarket can START" is not independently observable.  The decision's
#     window check is defence in depth: the schedule *value* already refuses to
#     permit a start outside regular hours, so removing the decision's half
#     changes no behaviour and the mutant survives by construction.  M11 mutates
#     the invariant itself, which is the guard that carries the property.
#
# This harness does not re-run the A1 mutants (26) or the earlier rounds'
# (E2/E3/E4/F1/F2/G1/G2-A/G2-B/FAC).  A1 is re-run separately as the historical
# gate because this layer depends on A1's vocabulary; the others are untouched
# because no file they anchor on was modified.
#
# Outcomes, and only `caught=True` counts as caught:
#
#   caught=True           the named tests ran and failed -- RED, as required;
#   caught=HARNESS-ERROR  the regex matched nothing, matched more than once, the
#                         mutation produced invalid Python, pytest exited 5, or
#                         the output had no "N passed|failed" line;
#   caught=ERROR          an unexpected exception while mutating or restoring.
#
# Run:
#
#     .\scripts\mutation_paper_autonomy_b1.ps1

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
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if (-not $onPath) {
        Write-Host "No Python interpreter found (.venv314 / .venv313 / .venv / PATH)." -ForegroundColor Red
        exit 2
    }
    return $onPath.Source
}

$py = Resolve-Python

& $py -c "import pytest, PySide6" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "The interpreter '$py' cannot import pytest / PySide6." -ForegroundColor Red
    exit 2
}

$src = Join-Path $projectRoot "src\us_quant"
$domainSupervisor = Join-Path $src "trading\domain\paper_autonomy_supervisor.py"
$supervisorApplication = Join-Path $src "trading\application\paper_autonomy_supervisor.py"
$portAction = Join-Path $src "trading\ports\paper_autonomy_action_repository.py"
$adapterAction = Join-Path $src "trading\adapters\sqlite\paper_autonomy_action_repository.py"
$scheduleAdapter = Join-Path $src "trading\adapters\paper_autonomy_schedule.py"
$executionOrchestrator = Join-Path $src "desktop_v2\orchestration\execution\orchestrator.py"
$paperOrchestrator = Join-Path $src "desktop_v2\orchestration\paper\orchestrator.py"
$desktop = Join-Path $src "desktop.py"
$autonomyExecutor = Join-Path $src "desktop_v2\orchestration\autonomy\executor.py"
$autonomyFacts = Join-Path $src "desktop_v2\orchestration\autonomy\facts.py"
$autonomyCompletion = Join-Path $src "desktop_v2\orchestration\autonomy\completion.py"
$autonomyHost = Join-Path $src "desktop_v2\orchestration\autonomy\host.py"
$autonomyRecovery = Join-Path $src "trading\application\paper_autonomy_recovery.py"
$actionRecovery = Join-Path $src "trading\adapters\sqlite\paper_autonomy_action_repository.py"
$supervisorDomain = Join-Path $src "trading\domain\paper_autonomy_supervisor.py"
$recoveryBehaviour = Join-Path $projectRoot "tests/test_paper_autonomy_recovery.py"

$behaviour = Join-Path $projectRoot "tests/test_paper_autonomy_supervisor.py"
$tickBehaviour = Join-Path $projectRoot "tests/test_paper_autonomy_supervisor_tick.py"
$architecture = Join-Path $projectRoot "tests/test_paper_autonomy_supervisor_architecture.py"
$launchBehaviour = Join-Path $projectRoot "tests/test_desktop_paper_launch_orchestrator.py"
$launchAuthorization = Join-Path $projectRoot "tests/test_desktop_paper_launch_authorization.py"
$completionBehaviour = Join-Path $projectRoot "tests/test_paper_autonomy_completion.py"
$scheduleBehaviour = Join-Path $projectRoot "tests/test_paper_autonomy_schedule.py"
$adapterBehaviour = Join-Path $projectRoot "tests/test_paper_autonomy_adapters.py"

function Get-Text([string]$path) { [System.IO.File]::ReadAllText($path) }
function Set-Text([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText(
        $path, $text, (New-Object System.Text.UTF8Encoding($false))
    )
}

# Every ``find`` below is runtime-verified to match **exactly one** location in
# its target (case-insensitively).  The harness does that check itself rather
# than relying on the author to keep the patterns unique: 0 matches and 2+
# matches are both HARNESS-ERROR.  This matters because the static
# ``[regex]::Replace(input, pattern, replacement, 1)`` overload binds that ``1``
# to ``RegexOptions`` (1 == IgnoreCase), **not** to a replacement count -- so a
# duplicated pattern would silently rewrite several sites at once and turn a
# single-point mutant into a multi-point one that still reported as caught.
$mutations = @(
    # -- a manual session is not under automation ------------------------
    @{
        name = 'M1  a manual session is stopped by the kill switch'
        file = $domainSupervisor
        find = 'foreign = _not_our_session\(\s+runtime,\s+day,\s+intent_revision,\s+manual_reason=\(\s+"the kill switch is latched, but the active Paper session is "\s+"manual and is not controlled by the supervisor"\s+\),\s+\)'
        repl = 'foreign = None'
        tests = @($behaviour)
        select = @("-k", "kill_switch_leaves_a_manual_session_alone")
    },
    @{
        name = 'M2  a manual paused session is resumed'
        file = $domainSupervisor
        find = 'foreign = _not_our_session\(\s+runtime,\s+day,\s+intent_revision,\s+manual_reason=\(\s+"autonomy is enabled, but the active Paper session is manual and is "\s+"not controlled by the supervisor"\s+\),\s+\)'
        repl = 'foreign = None'
        tests = @($behaviour)
        select = @("-k", "manual_session_is_not_resumed_either")
    },
    @{
        name = 'M2b a manual session is stopped when autonomy is disabled'
        file = $domainSupervisor
        find = 'foreign = _not_our_session\(\s+runtime,\s+day,\s+intent_revision,\s+manual_reason=\(\s+"autonomy is disabled, but the active Paper session is manual "\s+"and is not controlled by the supervisor"\s+\),\s+\)'
        repl = 'foreign = None'
        tests = @($behaviour)
        select = @("-k", "never_touches_a_manual_session")
    },
    @{
        name = 'M3  an unattributable session is treated as manual'
        file = $domainSupervisor
        find = 'if runtime\.session_provenance is PaperAutonomySessionProvenance\.UNKNOWN:'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "unattributable_session_blocks_and_is_never_controlled")
    },

    # -- the clock outranks the operator ---------------------------------
    @{
        name = 'M4  an autonomous session ignores the stop boundary'
        file = $domainSupervisor
        find = 'if schedule\.orderly_stop_due:\s+return _act\(\s+PaperAutonomyAction\.STOP,\s+"the orderly stop boundary has arrived; winding the autonomous "'
        repl = "if False:`n                return _act(`n                    PaperAutonomyAction.STOP,`n                    `"the orderly stop boundary has arrived; winding the autonomous `""
        tests = @($behaviour)
        select = @("-k", "autonomous_running_session_stops_at_the_boundary or autonomous_paused_session_stops_at_the_boundary")
    },
    @{
        name = 'M5  a paused intent ignores the stop boundary'
        file = $domainSupervisor
        find = 'if schedule\.orderly_stop_due:\s+return _act\(\s+PaperAutonomyAction\.STOP,\s+"the orderly stop boundary has arrived while the operator''s "\s+"intent is paused; winding the autonomous session down "'
        repl = "if False:`n                    return _act(`n                        PaperAutonomyAction.STOP,`n                        `"the orderly stop boundary has arrived while the operator''s `"`n                        `"intent is paused; winding the autonomous session down `""
        tests = @($behaviour)
        select = @("-k", "boundary_outranks_the_intent_but_not_a_kill")
    },

    # -- the action state machine ----------------------------------------
    @{
        name = 'M6  a claim may open an action in any state'
        file = $adapterAction
        find = 'if record\.status is not PaperAutonomyActionStatus\.CLAIMED:'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "a_claim_can_only_open_an_action")
    },
    @{
        name = 'M7  a second claim on one key overwrites the first'
        file = $adapterAction
        find = 'ON CONFLICT\(action_key\) DO NOTHING'
        repl = "ON CONFLICT(action_key) DO UPDATE SET status = excluded.status"
        tests = @($behaviour)
        select = @("-k", "action_key_can_be_claimed_exactly_once or two_concurrent_claims_produce_one_winner")
    },
    @{
        # Anchored on the Python status check, which is the *only* carrier of
        # "a request is accepted once": the read and the write share one
        # BEGIN IMMEDIATE, so the SQL guard that used to duplicate this rule was
        # removed rather than kept as an untestable second opinion.  Two mutants
        # were tried against the duplicated shape and both survived, each caught
        # by the other guard.
        name = 'M8  a request may be accepted twice'
        file = $adapterAction
        find = 'if claimed\.status is not PaperAutonomyActionStatus\.CLAIMED:'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "accepted_request_is_recorded_once")
    },
    @{
        name = 'M9  an action may succeed without its request being accepted'
        file = $adapterAction
        find = 'if \(\s+status is PaperAutonomyActionStatus\.SUCCEEDED\s+and stored\.status is PaperAutonomyActionStatus\.CLAIMED\s+\):'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "success_cannot_be_claimed_before_the_request_was_accepted")
    },
    @{
        name = 'M12 a non-regular window may permit preparation'
        file = $domainSupervisor
        find = 'if self\.preparation_allowed and \(\s+self\.session not in AUTONOMOUS_PREPARE_WINDOWS\s+\):'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "no_window_outside_the_prepare_set_can_permit_preparation")
    },
    @{
        name = 'M11 a non-regular window may permit a start'
        file = $domainSupervisor
        find = 'if self\.start_allowed and \(\s+self\.session not in AUTONOMOUS_START_WINDOWS\s+\):'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "no_window_outside_regular_can_permit_a_start")
    },

    # -- the ledger refuses what it cannot read --------------------------
    @{
        name = 'M10 the start query filters instead of reading the ledger'
        file = $adapterAction
        find = 'records = self\._read\(_SELECT_ALL, \(\)\)\s+return any\('
        repl = "records = self._read(f`"SELECT {_COLUMNS} FROM {TABLENAME} WHERE action = 'start'`", ())`n        return any("
        tests = @($behaviour)
        select = @("-k", "corrupt_row_makes_the_start_query_unreadable")
    },
    @{
        name = 'M15 a corrupt stored action revision is read as zero'
        file = $adapterAction
        find = 'raise PaperAutonomyActionStoreUnreadable\(\s+"the stored autonomy action revision is not an integer"\s+\) from error'
        repl = 'revision = 0'
        tests = @($behaviour)
        select = @("-k", "completing_refuses_a_corrupt_row or corrupt_row_makes_the_start_query_unreadable")
    },
    @{
        name = 'M14 an action record may name a negative revision'
        file = $portAction
        find = 'if self\.intent_revision < INITIAL_REVISION:'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "an_action_record_refuses_a_negative_revision")
    },
    @{
        name = 'M13 inconsistent Paper ownership still acts'
        file = $domainSupervisor
        find = 'if not runtime\.paper_ownership_consistent:'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "inconsistent_ownership_blocks_before_anything_acts")
    },

    # -- an unreadable calendar never opens anything ----------------------
    @{
        name = 'M17 an uncertain calendar resumes a paused session'
        file = $domainSupervisor
        find = 'if schedule\.exceptional_schedule_uncertain:\s+return _uncertain_active_session\('
        repl = "if schedule.exceptional_schedule_uncertain and runtime.session_running:`n            return _uncertain_active_session("
        tests = @($behaviour)
        select = @("-k", "uncertain_calendar_does_not_resume_an_autonomous_paused_session or uncertain_calendar_pauses_an_autonomous_running_session")
    },
    @{
        name = 'M18 an uncertain calendar leaves entries running'
        file = $domainSupervisor
        find = 'return _act\(\s+PaperAutonomyAction\.PAUSE_ENTRIES,\s+"the trading calendar could not be read; closing new entries while "'
        repl = "return _noop(`n            `"the trading calendar could not be read; closing new entries while `""
        tests = @($behaviour)
        select = @("-k", "uncertain_calendar_pauses_an_autonomous_running_session")
    },
    @{
        name = 'M19 an uncertain schedule may advertise a start'
        file = $domainSupervisor
        find = 'if self\.exceptional_schedule_uncertain and \(\s+self\.start_allowed or self\.preparation_allowed\s+\):'
        repl = 'if False:'
        tests = @($behaviour)
        select = @("-k", "uncertain_schedule_cannot_advertise_start_or_prepare")
    },

    # -- the capability-boundary guard itself ----------------------------
    # These three mutate the guard, not the product: what they have to prove is
    # that the coverage the review asked for is real rather than claimed.
    @{
        name = 'M20 the desktop autonomy subtree is not discovered'
        file = $architecture
        find = 'desktop = list\(autonomy_root\.rglob\("\*\.py"\)\) if autonomy_root\.exists\(\) else \[\]'
        repl = 'desktop = []'
        tests = @($architecture)
        select = @("-k", "discovery_covers_both_subtrees_and_nothing_else")
    },
    @{
        name = 'M21 function parameters are not inspected'
        file = $architecture
        find = 'if isinstance\(node, ast\.arg\):\s+return node\.arg'
        repl = "if False:`n        return None"
        tests = @($architecture)
        select = @("-k", "detector_catches_every_shape")
    },
    # -- the tick: claim, ask, record ------------------------------------
    @{
        # Anchored on the racing test, not the sequential one: sequentially the
        # *unresolved* clause stops the repeat before the claim is reached, so a
        # mutant that ignores the claim changes nothing there.  Measured -- the
        # first version of this entry targeted the sequential test and survived.
        name = 'M23 the claim is not consulted before the owner is asked'
        file = $supervisorApplication
        find = 'if not claimed:'
        repl = 'if False:'
        tests = @($tickBehaviour)
        select = @("-k", "two_ticks_racing_ask_the_owner_once")
    },
    @{
        name = 'M24 an accepted request is recorded as a success'
        file = $supervisorApplication
        find = 'self\._actions\.mark_requested\(\s+action_key=decision\.action_key, detail=outcome\.detail\s+\)'
        repl = "self._actions.complete(action_key=decision.action_key, status=PaperAutonomyActionStatus.SUCCEEDED, completed_at=moment, detail=outcome.detail)"
        tests = @($tickBehaviour)
        select = @("-k", "accepted_start_is_never_recorded_as_a_success or a_prepare_tick_claims_before_it_asks")
    },
    # -- the authority detector, again -----------------------------------
    @{
        name = 'M26 an annotated enum member is ignored'
        file = $architecture
        find = 'if isinstance\(node, ast\.AnnAssign\) and isinstance\(node\.target, ast\.Name\):\s+return \(node\.target\.id, node\.value\)'
        repl = "if False:`n        return None"
        tests = @($architecture)
        select = @("-k", "detector_catches_every_shape")
    },
    @{
        name = 'M27 an enum member name is matched exactly'
        file = $architecture
        find = 'if _announces_live_authority\(name\):'
        repl = 'if name in {"LIVE", "REAL_MONEY"}:'
        tests = @($architecture)
        select = @("-k", "detector_catches_every_shape")
    },

    # -- the tick's fail-closed semantics ---------------------------------
    @{
        name = 'M28 a raised owner call is recorded as a terminal failure'
        file = $supervisorApplication
        find = 'except Exception:  # noqa: BLE001 - the owner may raise anything'
        repl = "except Exception:  # noqa: BLE001 - the owner may raise anything`n            self._actions.complete(`n                action_key=decision.action_key,`n                status=PaperAutonomyActionStatus.FAILED,`n                completed_at=moment,`n                detail=`"the owner raised`",`n            )"
        tests = @($tickBehaviour)
        select = @("-k", "owner_exception_leaves_the_claim_unresolved or a_start_that_raised_is_never_replayed")
    },
    @{
        name = 'M29 an unreadable intent still reads the other ports'
        file = $supervisorApplication
        find = 'if not readable:'
        repl = 'if False:'
        tests = @($tickBehaviour)
        select = @("-k", "unreadable_intent_does_not_touch_any_other_port")
    },
    @{
        name = 'M30 an unreadable action ledger is treated as empty'
        file = $supervisorApplication
        find = 'return \(False, None, False, PaperAutonomyControlCycles\(\)\)'
        repl = 'return (True, None, False, PaperAutonomyControlCycles())'
        tests = @($tickBehaviour)
        select = @("-k", "unreadable_action_ledger_blocks or unreadable_ledger_blocks_even_when_only_the_day_query_fails")
    },
    @{
        name = 'M31 a routine no-op is reported as a warning'
        file = $supervisorApplication
        find = 'if decision\.action is PaperAutonomyAction\.NOOP:'
        repl = "if decision.action is PaperAutonomyAction.NOOP:`n            self._report(decision, code=PaperAutonomyEventCode.TICK_BLOCKED)"
        tests = @($tickBehaviour)
        select = @("-k", "a_tick_that_decides_nothing_asks_nobody or only_a_block_or_a_request_is_reported")
    },
    @{
        name = 'M32 schedule bypasses the canonical session provider'
        file = $scheduleAdapter
        find = 'canonical_session = us_equity_session\(eastern\)'
        repl = 'canonical_session = USEquitySession.REGULAR'
        tests = @($architecture)
        select = @("-k", "production_schedule_and_preparation_seams_stay_canonical")
    },
    @{
        name = 'M33 AFTER_HOURS permits autonomous START'
        file = $scheduleAdapter
        find = 'canonical_session is USEquitySession\.REGULAR'
        repl = 'canonical_session in {USEquitySession.REGULAR, USEquitySession.AFTER_HOURS}'
        tests = @($architecture)
        select = @("-k", "production_schedule_keeps_start_inside_regular_policy_window")
    },
    @{
        name = 'M34 the latest start boundary is ignored'
        file = $scheduleAdapter
        find = '<= self\._policy\.latest_start_et'
        repl = '<= time.max'
        tests = @($architecture)
        select = @("-k", "production_schedule_keeps_start_inside_regular_policy_window")
    },
    @{
        name = 'M35 orderly stop no longer closes new work'
        file = $scheduleAdapter
        find = 'eastern_time >= self\._policy\.orderly_stop_at_et'
        repl = 'eastern_time >= time.max'
        tests = @($scheduleBehaviour)
        select = @("-k", "orderly_stop_boundary_is_due_and_closes_new_work")
    },
    @{
        name = 'M36 automatic preparation reads the UI candidate limit'
        file = $executionOrchestrator
        find = 'limit = request\.candidate_limit'
        repl = 'limit = self._page.candidate_limit()'
        tests = @($architecture)
        select = @("-k", "execution_uses_one_request_driven_shortlist_implementation")
    },
    @{
        name = 'M37 automatic preparation reads the UI capital limit'
        file = $executionOrchestrator
        find = 'paper_capital, request\.capital_limit'
        repl = 'paper_capital, self._page.capital_limit()'
        tests = @($architecture)
        select = @("-k", "execution_uses_one_request_driven_shortlist_implementation")
    },
    @{
        name = 'M38 preparation duplicates the shortlist implementation call'
        file = $executionOrchestrator
        find = 'self\._build_shortlist\(request=request\)'
        repl = 'self._build_shortlist(request=request); self._build_shortlist(request=request)'
        tests = @($architecture)
        select = @("-k", "execution_uses_one_request_driven_shortlist_implementation")
    },
    @{
        name = 'M39 a refused autonomous launch clears the manual arm'
        file = $paperOrchestrator
        find = 'def _clear_arm_if_manual\(\s*self,\s*authorization: PaperLaunchAuthorization\s*\) -> None:\s+if authorization is PaperLaunchAuthorization\.MANUAL:\s+self\._clear_arm_confirmation\(\)'
        repl = 'def _clear_arm_if_manual(self, authorization: PaperLaunchAuthorization): return self._clear_arm_confirmation()'
        tests = @($launchBehaviour)
        select = @("-k", "refused_autonomous_start_does_not_clear_manual_arm")
    },
    @{
        name = 'M40 manual launch reads the A1 intent'
        file = $desktop
        find = 'if authorization is PaperLaunchAuthorization\.MANUAL:'
        repl = 'if authorization is PaperLaunchAuthorization.MANUAL and self.paper_autonomy_application.snapshot():'
        tests = @($launchAuthorization)
        select = @("-k", "manual_preflight_does_not_read_autonomy_intent")
    },
    @{
        name = 'M41 unreadable A1 permits autonomous confirmation'
        file = $desktop
        find = 'confirmed = False'
        repl = 'confirmed = True'
        tests = @($launchAuthorization)
        select = @("-k", "unreadable_a1_refuses_autonomous_confirmation")
    },
    @{
        name = 'M42 an unknown active session is adopted as autonomous'
        file = $autonomyFacts
        find = 'PaperAutonomySessionProvenance\.UNKNOWN'
        repl = 'PaperAutonomySessionProvenance.AUTONOMOUS'
        tests = @($adapterBehaviour)
        select = @("-k", "runtime_facts_are_fresh_and_active_restart_is_unknown")
    },
    @{
        name = 'M43 accepted RUNNING publication completes the wrong action'
        file = $autonomyCompletion
        find = 'PaperAutonomyActionType\.START,\s+PaperAutonomyActionStatus\.SUCCEEDED'
        repl = 'PaperAutonomyActionType.PREPARE, PaperAutonomyActionStatus.SUCCEEDED'
        tests = @($completionBehaviour)
        select = @("-k", "start_completes_only_from_running_publication")
    },
    @{
        name = 'M44 synchronous completion is lost before REQUESTED'
        file = $autonomyCompletion
        find = 'if row\.status is PaperAutonomyActionStatus\.CLAIMED:\s+self\._pending\[row\.action_key\] = completion\s+return'
        repl = 'if row.status is PaperAutonomyActionStatus.CLAIMED: return'
        tests = @($completionBehaviour)
        select = @("-k", "sync_publication_before_mark_requested_is_buffered_then_flushed")
    },
    @{
        name = 'M45 host takes over a business decision'
        file = $autonomyHost
        find = 'self\._supervisor\.tick\(\)'
        repl = 'self._supervisor.start()'
        tests = @($architecture)
        select = @("-k", "launch_authorization_host_and_completion_boundaries")
    },
    @{
        name = 'M46 a manual session is classified as autonomous'
        file = $autonomyFacts
        find = 'PaperAutonomySessionProvenance\.MANUAL'
        repl = 'PaperAutonomySessionProvenance.AUTONOMOUS'
        tests = @($adapterBehaviour)
        select = @("-k", "runtime_facts_are_fresh_and_active_restart_is_unknown")
    },
    @{
        name = 'M47 STOP completes before finalization'
        file = $autonomyCompletion
        find = 'PaperAutonomyActionType\.STOP'
        repl = 'PaperAutonomyActionType.START'
        tests = @($completionBehaviour)
        select = @("-k", "stop_completes_only_from_finalized_publication")
    },
    @{
        name = 'M48 autonomy reaches reconciliation directly'
        file = $autonomyCompletion
        find = 'from __future__ import annotations'
        repl = "from __future__ import annotations`n# reconcile("
        tests = @($architecture)
        select = @("-k", "desktop_autonomy_modules_do_not_bypass_paper_owners")
    },
    @{
        name = 'M49 autonomy releases the shared execution lease'
        file = $autonomyCompletion
        find = 'from __future__ import annotations'
        repl = "from __future__ import annotations`n# release_paper("
        tests = @($architecture)
        select = @("-k", "desktop_autonomy_modules_do_not_bypass_paper_owners")
    },
    @{
        name = 'M50 autonomy reaches the Market capability directly'
        file = $autonomyCompletion
        find = 'from __future__ import annotations'
        repl = "from __future__ import annotations`n# MarketOrchestrator"
        tests = @($architecture)
        select = @("-k", "desktop_autonomy_modules_do_not_bypass_paper_owners")
    },
    @{
        name = 'M51 the Qt host imports a broker module'
        file = $autonomyHost
        find = 'from __future__ import annotations'
        repl = "from __future__ import annotations`n# broker"
        tests = @($architecture)
        select = @("-k", "launch_authorization_host_and_completion_boundaries")
    },
    @{
        name = 'M52 runtime facts retain the workflow phase type'
        file = $autonomyFacts
        find = 'from __future__ import annotations'
        repl = "from __future__ import annotations`n# PaperWorkflowPhase"
        tests = @($architecture)
        select = @("-k", "desktop_autonomy_modules_do_not_bypass_paper_owners")
    },
    @{
        name = 'M53 a persistent calendar failure escapes the schedule adapter'
        file = $scheduleAdapter
        find = 'return PaperAutonomyScheduleFacts\(\s+trading_day=None,\s+session=None,\s+preparation_allowed=False,\s+start_allowed=False,\s+orderly_stop_due=False,\s+exceptional_schedule_uncertain=True,\s+action_day=eastern\.date\(\),\s+\)'
        repl = 'raise PaperAutonomySupervisorError("calendar unavailable")'
        tests = @($scheduleBehaviour, $tickBehaviour)
        select = @("-k", "persistent_calendar_failure_returns_uncertain_facts_without_retry or persistent_calendar_failure_still_pauses_autonomous_entries")
    },
    @{
        name = 'M54 a second pause reuses the first pause cycle'
        file = $domainSupervisor
        find = 'intent_revision,\s+control_cycles\.pause_attempt,\s+\)'
        repl = "intent_revision,`n                0,`n            )"
        tests = @($tickBehaviour)
        select = @("-k", "pause_resume_pause_uses_durable_control_cycles")
    },
    @{
        name = 'M55 a second resume reuses the first resume cycle'
        file = $domainSupervisor
        find = 'attempt=control_cycles\.resume_attempt,'
        repl = 'attempt=0,'
        tests = @($tickBehaviour)
        select = @("-k", "pause_resume_pause_uses_durable_control_cycles")
    },
    @{
        name = 'M56 a terminal refused safety action is silently ignored'
        file = $supervisorApplication
        find = 'if existing\.is_terminal and action_type in \{'
        repl = 'if False and existing.is_terminal and action_type in {'
        tests = @($tickBehaviour)
        select = @("-k", "refused_pause_cycle_blocks_when_pause_is_still_required")
    },
    @{
        name = 'M57 an unrelated publication conflicts with a prior terminal action'
        file = $autonomyCompletion
        find = 'if not matches:\s+return'
        repl = "if not matches:`n            raise PaperAutonomyActionRepositoryError(`"unattributable completion`")"
        tests = @($completionBehaviour)
        select = @("-k", "manual_running_after_failed_autonomous_start_is_ignored or manual_launch_failure_after_successful_start_is_ignored or manual_prepare_ready_after_failed_autonomous_prepare_is_ignored")
    },
    @{
        name = 'M58 manual RESUME does not advance a later PAUSE identity'
        file = $adapterAction
        find = 'pause_attempt=pauses,'
        repl = 'pause_attempt=resumes,'
        tests = @($tickBehaviour)
        select = @("-k", "manual_resume_does_not_reuse_a_successful_pause_key")
    },
    @{
        name = 'M59 manual PAUSE does not advance a later RESUME identity'
        file = $adapterAction
        find = 'resume_attempt=resumes,'
        repl = 'resume_attempt=pauses,'
        tests = @($tickBehaviour)
        select = @("-k", "manual_pause_does_not_reuse_a_successful_resume_key")
    },
    @{
        name = 'M60 an unresolved action is considered startup-safe'
        file = $domainSupervisor
        find = 'and self\.unresolved_action_count == 0'
        repl = 'and True'
        tests = @($behaviour, $adapterBehaviour)
        select = @("-k", "startup_rejects_unresolved_or_unknown_action_count or startup_action_count_blocks_when_previous_action_is_unresolved")
    },
    @{
        name = 'M61 an unsafe startup still wires old completion actions'
        file = $desktop
        find = 'if not startup\.proven_safe:'
        repl = 'if False and not startup.proven_safe:'
        tests = @($architecture)
        select = @("-k", "startup_with_unresolved_action_never_wires_completion_or_starts_host")
    },
    @{
        name = 'M62 unknown action resolution is allowed while autonomy is enabled'
        file = $autonomyRecovery
        find = 'if intent\.mode is not PaperAutonomyMode\.DISABLED:'
        repl = 'if False:'
        tests = @($recoveryBehaviour)
        select = @("-k", "resolve_requires_disabled_intent")
    },
    @{
        name = 'M63 operator resolution is written as canonical success'
        file = $actionRecovery
        find = 'PaperAutonomyActionStatus\.OPERATOR_RESOLVED\.value'
        repl = 'PaperAutonomyActionStatus.SUCCEEDED.value'
        tests = @($recoveryBehaviour)
        select = @("-k", "operator_resolution_is_not_success")
    },
    @{
        name = 'M64 resolution ignores the expected current status'
        file = $actionRecovery
        find = 'if stored\.status is not expected_status:'
        repl = 'if False:'
        tests = @($recoveryBehaviour)
        select = @("-k", "resolution_uses_expected_status")
    },
    @{
        name = 'M65 resolution deletes the action history row'
        file = $actionRecovery
        find = 'cursor = connection\.execute\(\s+f"""\s+UPDATE \{TABLENAME\}\s+SET status = \?, completed_at = \?, detail = \?\s+WHERE action_key = \?\s+""",\s+\(\s+PaperAutonomyActionStatus\.OPERATOR_RESOLVED\.value,\s+to_stored_text\(resolved_at\),\s+detail,\s+action_key,\s+\),\s+\)'
        repl = 'cursor = connection.execute(f"DELETE FROM {TABLENAME} WHERE action_key = ?", (action_key,))'
        tests = @($recoveryBehaviour)
        select = @("-k", "history_is_preserved_as_operator_resolved")
    },
    @{
        name = 'M66 an operator-resolved START no longer counts against the day'
        file = $actionRecovery
        find = 'record\.trading_day == trading_day\s+and record\.action is PaperAutonomyActionType\.START'
        repl = "record.trading_day == trading_day`n            and record.action is PaperAutonomyActionType.START`n            and record.status is PaperAutonomyActionStatus.SUCCEEDED"
        tests = @($recoveryBehaviour)
        select = @("-k", "resolved_start_still_counts_as_attempted")
    },
    @{
        name = 'M67 an unsafe process falls through to completion signal wiring'
        file = $desktop
        find = '            \)\s+return\s+\s+def reset_block_event_after_recovery'
        repl = "            )`n            pass`n`n        def reset_block_event_after_recovery"
        tests = @($recoveryBehaviour)
        select = @("-k", "unsafe_startup_returns_before_completion_signals_are_connected")
    },
    @{
        name = 'M68 authorization at the recovery timestamp is treated as later'
        file = $supervisorDomain
        find = 'return authorization_updated_at > latest_resolution_at'
        repl = 'return authorization_updated_at >= latest_resolution_at'
        tests = @($recoveryBehaviour)
        select = @("-k", "recovery_authorization_timestamp_comparison_is_strict")
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
    $mutated = $null
    try {
        $regex = [regex]::new(
            $mutation.find,
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
        $patternMatches = $regex.Matches($original)
        if ($patternMatches.Count -ne 1) {
            $caught = "HARNESS-ERROR"
            $detail = "the mutation pattern matched $($patternMatches.Count) locations; expected exactly 1"
        }
        else {
            $mutated = $regex.Replace($original, $mutation.repl, 1)
        }
        if ($null -eq $caught -and $mutated -eq $original) {
            $caught = "HARNESS-ERROR"
            $detail = "the mutation applied nothing (the replacement reproduced the original text)"
        }
        if ($null -eq $caught) {
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
                $countLine = $output | Select-String -Pattern '\d+ (passed|failed)' | Select-Object -Last 1
                if ($exit -eq 5) {
                    $caught = "HARNESS-ERROR"
                    $detail = "no tests were selected by $($mutation.select -join ' ')"
                }
                elseif ($exit -notin 0, 1) {
                    $caught = "HARNESS-ERROR"
                    $detail = "pytest exited $exit (collection/usage/internal error), not an assertion failure"
                }
                elseif (-not $countLine) {
                    $caught = "HARNESS-ERROR"
                    $detail = "no test count in the output"
                }
                else {
                    $caught = ($exit -ne 0)
                    $detail = $countLine
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
    Write-Host ("{0,-64} caught={1}  {2}" -f $mutation.name, $caught, $detail)
}

$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) {
    Write-Host "  SURVIVED: $($row.Mutation) -> $($row.Detail)"
}

if ($uncaught.Count -gt 0) { exit 1 }
exit 0
