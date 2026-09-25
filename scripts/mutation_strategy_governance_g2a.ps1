# Mutation verification for G2-A (strategy governance orchestration).
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
# ``desktop.py`` or the strategy orchestration package must re-run every
# harness in this directory.  A pattern that matches nothing is reported as
# HARNESS-ERROR and fails the script, so a dead anchor can never masquerade as
# a green gate.
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
#     .\scripts\mutation_strategy_governance_g2a.ps1

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
$strategyDir = Join-Path $projectRoot "src\us_quant\desktop_v2\orchestration\strategy"
$orchestratorPath = Join-Path $strategyDir "orchestrator.py"
$queriesPath = Join-Path $strategyDir "queries.py"

$g2aGuards = Join-Path $projectRoot "tests/test_desktop_strategy_governance_architecture.py"
$orchestration = Join-Path $projectRoot "tests/test_desktop_strategy_governance_orchestration.py"
$wiring = Join-Path $projectRoot "tests/test_desktop_strategy_wiring.py"

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
        name = 'M1  the window renders the strategy page directly again'
        file = $desktopPath
        find = '        self\.strategy_page = StrategyPage\(palette=self\.theme\)'
        repl = "        self.strategy_page = StrategyPage(palette=self.theme)`n        self.strategy_page.render(self.strategies.list_versions())"
        tests = @($g2aGuards, $wiring)
        select = @("-k", "window_never_renders_the_strategy_page or strategy_render_caller_is_the_capability")
    },
    @{
        name = 'M2  an invalid JSON clone still reaches the application'
        file = $orchestratorPath
        # The refusal branch is dropped and an empty parameter set is what the
        # application receives, so the ``clone_calls == []`` assertion is the
        # killer.
        find = '        except json\.JSONDecodeError as error:\r?\n            self\.warning_requested\.emit\(\r?\n                ("[^"]*", f"[^"]*")\r?\n            \)\r?\n            return'
        repl = "        except json.JSONDecodeError:`n            parameters = {}"
        tests = @($orchestration)
        select = @("-k", "invalid_json_clone")
    },
    @{
        name = 'M3  a non-object JSON clone is accepted'
        file = $queriesPath
        find = '    if not isinstance\(parameters, dict\):\r?\n        raise ValueError\(("[^"]*")\)'
        repl = "    if False:`n        raise ValueError(`$1)"
        tests = @($orchestration)
        select = @("-k", "non_object_json_clone")
    },
    @{
        name = 'M4  a refused clone still publishes success'
        file = $orchestratorPath
        # Swallowing the application error makes the failure path fall through
        # to the refresh + success log; the sentinel ``list_calls`` and the
        # empty-warning assertions are the killers.
        find = '        except \(ValueError, StrategyApplicationError\) as error:\r?\n            (self\.warning_requested\.emit\(.*\))\r?\n            return'
        repl = "        except (ValueError, StrategyApplicationError):`n            pass"
        tests = @($orchestration)
        select = @("-k", "refused_clone_publishes_no_success")
    },
    @{
        name = 'M5  a successful clone does not refresh the catalogue'
        file = $orchestratorPath
        find = '        self\.refresh\(\)\r?\n        (self\.log_requested\.emit\(\r?\n            f"[^"]*"\r?\n            "[^"]*"\r?\n        \))'
        repl = "        pass`n        `$1"
        tests = @($orchestration)
        select = @("-k", "successful_clone_refreshes")
    },
    @{
        name = 'M6  a blocked transition still writes STATUS_CHANGE'
        file = $orchestratorPath
        # The operator copy is preserved via capture groups; only the runtime
        # event is added -- which is exactly the forbidden behaviour.
        find = '        except StrategyApplicationError as error:\r?\n            (self\.warning_requested\.emit\(\r?\n                "[^"]*",\r?\n                f"[^"]*",\r?\n            \))\r?\n            (self\.log_requested\.emit\(f"[^"]*"\))\r?\n            return'
        repl = "        except StrategyApplicationError as error:`n            `$1`n            `$2`n            self.runtime_event_requested.emit(`n                StrategyRuntimeEvent(`n                    severity=`"info`",`n                    component=`"strategy`",`n                    code=`"STATUS_CHANGE`",`n                    message=`"mutant status change`",`n                )`n            )`n            return"
        tests = @($orchestration)
        select = @("-k", "blocked_transition_publishes_no_runtime_event")
    },
    @{
        name = 'M7  a successful transition does not refresh the catalogue'
        file = $orchestratorPath
        # The transition success log is the single-argument multi-line form
        # (the clone's carries a second string line, which M5 anchors on).
        find = '        self\.refresh\(\)\r?\n        (self\.log_requested\.emit\(\r?\n            f"[^"]*"\r?\n        \))'
        repl = "        pass`n        `$1"
        tests = @($orchestration)
        select = @("-k", "successful_transition_changes_status_exactly_once")
    },
    @{
        name = 'M8  viewing a version repoints the runtime selection'
        file = $orchestratorPath
        # The governance path must never reach any ``select``: the fake
        # application's ``select`` trap raises, and the AST guard fails on the
        # call shape itself.
        find = '            version = self\._application\.get_version\(version_id\)'
        repl = "            self._application.select(version_id)`n            version = self._application.get_version(version_id)"
        tests = @($orchestration, $g2aGuards)
        select = @("-k", "never_selects_for_runtime or viewing_a_version_publishes_a_notice")
    },
    @{
        name = 'M9  the orchestrator caches a second catalogue'
        file = $orchestratorPath
        find = '        self\._page\.render\(versions\)\r?\n        self\.catalog_changed\.emit\(\)'
        repl = "        self._versions = versions`n        self._page.render(versions)`n        self.catalog_changed.emit()"
        tests = @($g2aGuards)
        select = @("-k", "orchestrator_caches_no_catalogue")
    },
    @{
        name = 'M10 the strategy capability imports another capability'
        file = $orchestratorPath
        find = 'from us_quant\.trading\.application\.strategies import \('
        repl = "from us_quant.desktop_v2.orchestration.paper import PaperOrchestrator`nfrom us_quant.trading.application.strategies import ("
        tests = @($g2aGuards)
        select = @("-k", "imports_no_other_capability")
    },
    @{
        name = 'M11 the catalogue-changed announcement is lost'
        file = $orchestratorPath
        find = '        self\.catalog_changed\.emit\(\)'
        repl = "        pass"
        tests = @($orchestration)
        select = @("-k", "refresh_reads_paints_and_announces or fans_out_to_every_refilling_route")
    },
    @{
        name = 'M12 strategy governance logic moves back into the window'
        file = $desktopPath
        find = '    def _show_strategy_warning\(self, title: str, message: str\) -> None:'
        repl = "    def _refresh_strategy_page(self) -> None:`n        self.strategy_page.render(self.strategies.list_versions())`n`n    def _show_strategy_warning(self, title: str, message: str) -> None:"
        tests = @($g2aGuards)
        select = @("-k", "retired_window_handlers_are_gone")
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
