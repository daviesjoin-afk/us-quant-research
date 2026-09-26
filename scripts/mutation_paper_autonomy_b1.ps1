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
$portAction = Join-Path $src "trading\ports\paper_autonomy_action_repository.py"
$adapterAction = Join-Path $src "trading\adapters\sqlite\paper_autonomy_action_repository.py"

$behaviour = Join-Path $projectRoot "tests/test_paper_autonomy_supervisor.py"
$architecture = Join-Path $projectRoot "tests/test_paper_autonomy_supervisor_architecture.py"

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
