# Mutation verification for the Paper autonomy control plane (v1-A).
#
# Each entry breaks exactly one property the control plane has to have before an
# unattended process may read it, then asserts that the guard or behaviour test
# naming that property actually fails.  A mutation that survives is a hole:
# either the guard is not testing what it claims, or the property is not locked.
#
# What this round is about is a store of *operator intent*: it must default away
# from trading, it must refuse a stale writer instead of absorbing one, it must
# latch off in a way that clearing cannot undo, and it must not become a second
# home for a runtime fact.  So the mutants below move the default, remove each
# guard in turn, and -- the interesting half -- try to make the control plane
# *hold* something it must read live.
#
# This harness deliberately does not re-run the earlier rounds' mutants (E2 13,
# E3 41, E4 11, F1 14, F2 22, G1 17, G2-A 12, G2-B 35, FAC 42).  Those are
# re-run separately as the historical gate; duplicating them here would double
# the wall time and tell us nothing new.
#
# The harness distinguishes three outcomes, and only `caught=True` counts as a
# caught mutation:
#
#   caught=True           the named tests ran and failed -- RED, as required;
#   caught=HARNESS-ERROR  the regex matched nothing, matched more than once, the
#                         mutation produced invalid Python, pytest exited 5 ("no
#                         tests collected"), or the output had no
#                         "N passed|failed" line.  Any of those is a hole in
#                         *this* script, and reporting one as "caught" would let
#                         a broken mutation look like a working guard;
#   caught=ERROR          an unexpected exception while mutating or restoring.
#
# A pattern that matches nothing is reported as HARNESS-ERROR and fails the
# script, so a dead anchor can never masquerade as a green gate.
#
# Run:
#
#     .\scripts\mutation_paper_autonomy_a1.ps1

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
$domainAutonomy = Join-Path $src "trading\domain\paper_autonomy.py"
$portAutonomy = Join-Path $src "trading\ports\paper_autonomy_repository.py"
$applicationAutonomy = Join-Path $src "trading\application\paper_autonomy.py"
$adapterAutonomy = Join-Path $src "trading\adapters\sqlite\paper_autonomy_repository.py"
$cli = Join-Path $src "cli.py"

$autonomyBehaviour = Join-Path $projectRoot "tests/test_paper_autonomy.py"
$autonomyArch = Join-Path $projectRoot "tests/test_paper_autonomy_architecture.py"

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
# The instance overload ``$regex.Replace(input, replacement, 1)`` used below
# takes a real count, and the preceding match check makes it exact.
$mutations = @(
    # -- the default state ---------------------------------------------
    @{
        name = 'M1  a store nobody wrote reads as ENABLED'
        file = $domainAutonomy
        find = 'mode=PaperAutonomyMode\.DISABLED,'
        repl = "mode=PaperAutonomyMode.ENABLED,"
        tests = @($autonomyBehaviour, $autonomyArch)
        select = @("-k", "store_nobody_configured_is_disabled")
    },

    # -- the kill switch ------------------------------------------------
    @{
        name = 'M2  the kill switch latches without moving the mode'
        file = $applicationAutonomy
        find = 'mode=PaperAutonomyMode\.DISABLED,\s+kind=PaperAutonomyEventKind\.KILL_LATCHED,'
        repl = "mode=current.mode,`n            kind=PaperAutonomyEventKind.KILL_LATCHED,"
        tests = @($autonomyBehaviour)
        select = @("-k", "killing_an_enabled_intent_disables_it or killing_a_paused_intent_disables_it")
    },
    @{
        name = 'M3  enable() ignores the kill-switch latch'
        file = $applicationAutonomy
        find = 'if current\.kill_switch_latched:\s+raise PaperAutonomyRefused\(\s+"the kill switch is latched; autonomy cannot be re-enabled'
        repl = "if False:`n            raise PaperAutonomyRefused(`n                `"the kill switch is latched; autonomy cannot be re-enabled"
        tests = @($autonomyBehaviour, $autonomyArch)
        select = @("-k", "enabling_while_latched_is_refused or the_kill_switch_cannot_be_bypassed")
    },
    @{
        name = 'M4  clearing the latch silently enables'
        file = $applicationAutonomy
        find = 'mode=current\.mode,'
        repl = "mode=PaperAutonomyMode.ENABLED,"
        tests = @($autonomyBehaviour, $autonomyArch)
        select = @("-k", "clearing_the_latch_does_not_re_enable or the_kill_switch_cannot_be_bypassed")
    },

    # -- the stale writer ------------------------------------------------
    @{
        name = 'M5  the store writes regardless of the expected revision'
        file = $adapterAutonomy
        find = 'if stored != expected_revision:'
        repl = 'if False:'
        tests = @($autonomyBehaviour)
        select = @("-k", "stale_writer_is_refused_and_changes_nothing or concurrent_writers_produce_exactly_one_transition")
    },
    @{
        name = 'M6  the authority never compares the revision it was given'
        file = $applicationAutonomy
        find = 'if current\.revision != expected_revision:'
        repl = 'if False:'
        tests = @($autonomyBehaviour)
        select = @("-k", "stale_writer_is_refused_and_changes_nothing")
    },

    # -- one transition, one commit ---------------------------------------
    @{
        name = 'M7  the store commits the intent without recording it'
        file = $adapterAutonomy
        find = 'VALUES \(\?, \?, \?, \?\)'
        repl = 'SELECT ?, ?, ?, ? WHERE 0'
        tests = @($autonomyBehaviour)
        select = @("-k", "enable_advances_one_revision_and_records_one_event or the_event_trail_replays_the_revisions_in_order")
    },
    @{
        name = 'M14 the intent is committed before the event insert'
        file = $adapterAutonomy
        find = 'connection\.execute\(\s+"""\s+INSERT INTO paper_autonomy_events\('
        repl = "connection.execute(`"COMMIT`")`n                connection.isolation_level = None`n                connection.execute(`"BEGIN IMMEDIATE`")`n                connection.execute(`n                    `"`"`"`n                    INSERT INTO paper_autonomy_events("
        tests = @($autonomyBehaviour)
        select = @("-k", "failed_audit_write_rolls_the_intent_back")
    },
    @{
        name = 'M17 the pair check ignores the event revision'
        file = $adapterAutonomy
        find = 'if event\.revision != replacement\.revision:'
        repl = 'if False:'
        tests = @($autonomyBehaviour)
        select = @("-k", "refuses_a_pair_that_is_not_one_transition")
    },
    # -- the stored history has to describe the stored intent -------------
    @{
        name = 'M15 an orphaned history reads as a fresh revision 0'
        file = $adapterAutonomy
        find = 'if count:'
        repl = 'if False:'
        tests = @($autonomyArch)
        select = @("-k", "orphaned_history_is_not_a_fresh_store")
    },
    @{
        name = 'M18 a gap in the audit trail is accepted'
        file = $adapterAutonomy
        find = 'if count != revision or lowest != 1 or highest != revision:'
        repl = 'if lowest != 1 or highest != revision:'
        tests = @($autonomyBehaviour)
        select = @("-k", "a_gap_in_the_audit_trail")
    },

    # -- a transition that did nothing is not a transition ----------------
    @{
        name = 'M16 clear-kill succeeds while the latch is already clear'
        file = $applicationAutonomy
        find = 'if not current\.kill_switch_latched:'
        repl = 'if False:'
        tests = @($autonomyBehaviour)
        select = @("-k", "clear_kill_is_refused_when_the_latch_is_not_set")
    },

    # -- one truth -------------------------------------------------------
    @{
        name = 'M8  the store decides the mode it stores'
        file = $adapterAutonomy
        find = 'str\(replacement\.mode\),'
        repl = 'PaperAutonomyMode.ENABLED.value,'
        tests = @($autonomyBehaviour)
        select = @("-k", "restart_restores_the_stored_intent or the_event_trail_replays_the_revisions_in_order")
    },
    @{
        name = 'M9  the authority imports the Paper workflow phase'
        file = $applicationAutonomy
        find = 'from dataclasses import replace'
        repl = "from dataclasses import replace`nfrom us_quant.trading.runtime.workflow_state import PaperWorkflowPhase  # mutation"
        tests = @($autonomyArch)
        select = @("-k", "depends_on_the_domain_and_the_ports_only or names_no_paper_lifecycle_or_execution_authority")
    },
    @{
        name = 'M10 the intent persists a candidate shortlist'
        file = $domainAutonomy
        find = '\n    reason: str\n'
        repl = "`n    reason: str`n    candidate_symbols: tuple[str, ...] = ()`n"
        tests = @($autonomyArch)
        select = @("-k", "carries_no_runtime_truth")
    },
    @{
        name = 'M11 the intent persists the selected strategy version'
        file = $domainAutonomy
        find = '\n    reason: str\n'
        repl = "`n    reason: str`n    strategy_version_id: str = `"`"`n"
        tests = @($autonomyArch)
        select = @("-k", "carries_no_runtime_truth")
    },

    # -- reading a stored value ------------------------------------------
    @{
        name = 'M12 an unknown stored mode falls back to ENABLED'
        file = $adapterAutonomy
        find = 'mode = PaperAutonomyMode\(str\(mode_text\)\)'
        repl = 'mode = PaperAutonomyMode(str(mode_text)) if str(mode_text) in {member.value for member in PaperAutonomyMode} else PaperAutonomyMode.ENABLED'
        tests = @($autonomyBehaviour)
        select = @("-k", "corrupt_stored_intent_is_never_read_as_enabled")
    },

    # -- the operator surface --------------------------------------------
    @{
        name = 'M13 the CLI reaches the store instead of the authority'
        file = $cli
        find = 'current = application\.snapshot\(\)'
        repl = 'current = application._repository.load_intent()'
        tests = @($autonomyBehaviour)
        select = @("-k", "the_cli_is_a_surface_and_not_a_second_authority")
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
    Write-Host ("{0,-62} caught={1}  {2}" -f $mutation.name, $caught, $detail)
}

$uncaught = @($results | Where-Object { $_.Caught -ne $true })
Write-Host "mutations: $($results.Count)   red: $(@($results | Where-Object { $_.Caught -eq $true }).Count)   not caught: $($uncaught.Count)"
foreach ($row in $uncaught) {
    Write-Host "  SURVIVED: $($row.Mutation) -> $($row.Detail)"
}

if ($uncaught.Count -gt 0) { exit 1 }
exit 0
