# Mutation verification for v2O-F2 (Settings orchestration).
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
# The script is repo-root anchored, checks its interpreter explicitly, and exits
# non-zero if any mutation survived or any harness error occurred -- the standard
# the F1 script established after the E3 gate was found silently degraded.
#
# Run:
#
#     .\scripts\mutation_system_settings_f2.ps1

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

$settingsDir = Join-Path $projectRoot "src\us_quant\desktop_v2\orchestration\system\settings"
$orchestrator = Join-Path $settingsDir "orchestrator.py"
$queries = Join-Path $settingsDir "queries.py"
$desktopPath = Join-Path $projectRoot "src\us_quant\desktop.py"

$behaviour = Join-Path $projectRoot "tests/test_desktop_settings_orchestrator.py"
$pure = Join-Path $projectRoot "tests/test_desktop_settings_queries.py"
$architecture = Join-Path $projectRoot "tests/test_desktop_settings_orchestration_architecture.py"
$wiring = Join-Path $projectRoot "tests/test_desktop_v2_settings_wiring.py"
$regression = Join-Path $projectRoot "tests/test_desktop_settings_provider_switch_regression.py"

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
        name = "M1  the window keeps the selected API provider again"
        file = $desktopPath
        find = "        page = self\.settings_page"
        repl = "        self._settings_api_provider = ""ibkr""`n        page = self.settings_page"
        tests = @($architecture)
        select = @("-k", "no_settings_presentation_state or real_window_holds_no_settings_state")
    },
    @{
        name = "M2  the window keeps the connection-control fact again"
        file = $desktopPath
        find = "        page = self\.settings_page"
        repl = "        self._connection_settings_enabled = True`n        page = self.settings_page"
        tests = @($architecture)
        select = @("-k", "no_settings_presentation_state or both_presentation_facts")
    },
    @{
        name = "M3  the render caches the credential status"
        file = $orchestrator
        find = "        provider = self\._selected_api_provider\r?\n        requires_api_key = provider_requires_api_key\(provider\)\r?\n        self\._page\.render\(\r?\n            SettingsPageView\(\r?\n                credential_status_text=credential_status_text\(\r?\n                    self\._credential_service\.status\(provider\)\r?\n                \),"
        repl = "        provider = self._selected_api_provider`n        requires_api_key = provider_requires_api_key(provider)`n        if getattr(self, ""_mutant_status_provider"", None) != provider:`n            self._mutant_status_provider = provider`n            self._mutant_status_text = credential_status_text(`n                self._credential_service.status(provider)`n            )`n        self._page.render(`n            SettingsPageView(`n                credential_status_text=self._mutant_status_text,"
        tests = @($behaviour, $architecture)
        select = @("-k", "status_and_the_live_source or caches_no_application_state")
    },
    @{
        name = "M4  the live market source is captured at construction"
        file = $orchestrator
        find = "        self\._active_market_source_id = active_market_source_id"
        repl = "        _mutant_initial_source = active_market_source_id()`n        self._active_market_source_id = lambda: _mutant_initial_source"
        tests = @($behaviour)
        select = @("-k", "live_source_is_read_at_clear_time")
    },
    @{
        name = "M5  the live-source credential gate is gone"
        file = $orchestrator
        find = "        if provider == self\._active_market_source_id\(\):\r?\n            self\.warning_requested\.emit\(\r?\n                ACTIVE_SOURCE_TITLE, ACTIVE_SOURCE_MESSAGE\r?\n            \)\r?\n            return\r?\n"
        repl = ""
        tests = @($behaviour, $wiring)
        select = @("-k", "clearing_the_live_providers or active_provider_credentials")
    },
    @{
        name = "M6  a half-filled Alpaca pair is accepted"
        file = $queries
        find = "        if not trimmed_key or not trimmed_secret:\r?\n            return CredentialSavePlan\(\r?\n                provider=provider, outcome=CredentialSaveOutcome\.INCOMPLETE\r?\n            \)\r?\n"
        repl = ""
        tests = @($pure, $behaviour)
        select = @("-k", "half_filled_alpaca")
    },
    @{
        name = "M7  a saved credential is neither cleared nor repainted"
        file = $orchestrator
        find = "        self\._page\.clear_credential_inputs\(\)\r?\n        self\.render_current\(\)\r?\n        self\.log_requested\.emit\(CREDENTIAL_SAVED_LOG\)"
        repl = "        self.log_requested.emit(CREDENTIAL_SAVED_LOG)"
        tests = @($behaviour)
        select = @("-k", "finnhub_key_writes_once or complete_alpaca_pair_is_written")
    },
    @{
        name = "M8  a failed credential save reports success"
        file = $orchestrator
        find = "        except \(CredentialStoreError, OSError, ValueError\) as error:\r?\n            self\.warning_requested\.emit\(\r?\n                CREDENTIAL_SAVE_FAILED_TITLE, str\(error\)\r?\n            \)\r?\n            return\r?\n        self\._page\.clear_credential_inputs\(\)"
        repl = "        except (CredentialStoreError, OSError, ValueError) as error:`n            self._page.clear_credential_inputs()`n        self._page.clear_credential_inputs()"
        tests = @($behaviour)
        select = @("-k", "failed_credential_save")
    },
    @{
        name = "M9  a refused preference commit is published as a success"
        file = $orchestrator
        find = "            self\.warning_requested\.emit\(SAVE_FAILED_TITLE, str\(error\)\)\r?\n            return None"
        repl = "            self.warning_requested.emit(SAVE_FAILED_TITLE, str(error))`n            return DesktopSettingsCommit(`n                preferences=preferences, config=self._current_config()`n            )"
        tests = @($behaviour)
        select = @("-k", "refused_preference_save")
    },
    @{
        name = "M10 a refused commit still asks for the market switch"
        file = $orchestrator
        find = "        commit = self\._commit\(draft\)\r?\n        if commit is None:\r?\n            return\r?\n        self\.settings_committed\.emit\(commit\)\r?\n        self\.market_switch_requested\.emit\(\r?\n            commit\.preferences\.market_provider\r?\n        \)"
        repl = "        commit = self._commit(draft)`n        if commit is None:`n            self.market_switch_requested.emit(draft.market_provider)`n            return`n        self.settings_committed.emit(commit)`n        self.market_switch_requested.emit(commit.preferences.market_provider)"
        tests = @($regression, $behaviour)
        select = @("-k", "refused_save_does_not_switch or refused_save_does_not_move or refused_provider_switch")
    },
    @{
        name = "M11 the switch is published before the commit fact"
        file = $orchestrator
        find = "        commit = self\._commit\(draft\)\r?\n        if commit is None:\r?\n            return\r?\n        self\.settings_committed\.emit\(commit\)\r?\n        self\.market_switch_requested\.emit\(\r?\n            commit\.preferences\.market_provider\r?\n        \)"
        repl = "        commit = self._commit(draft)`n        if commit is None:`n            return`n        self.market_switch_requested.emit(commit.preferences.market_provider)`n        self.settings_committed.emit(commit)"
        tests = @($regression)
        select = @("-k", "switch_request_is_published_after_the_commit_fact")
    },
    @{
        name = "M12 the programmatic Market -> Settings sync emits an intent"
        file = $orchestrator
        find = "        self\._page\.set_market_provider\(provider, emit_change=False\)"
        repl = "        self._page.set_market_provider(provider, emit_change=True)"
        tests = @($architecture, $behaviour)
        select = @("-k", "programmatic_page_write_is_silent or adopting_the_market_provider")
    },
    @{
        name = "M13 selecting a market provider stops syncing the API provider"
        file = $orchestrator
        find = "        api_provider = api_provider_for_market_provider\(provider\)\r?\n        self\._selected_api_provider = api_provider\r?\n        self\._page\.set_api_provider\(api_provider, emit_change=False\)\r?\n"
        repl = ""
        tests = @($behaviour)
        select = @("-k", "selecting_a_market_provider")
    },
    @{
        name = "M14 the connection fact changes without a repaint"
        file = $orchestrator
        find = "        self\._connection_settings_enabled = enabled\r?\n        self\.render_current\(\)"
        repl = "        self._connection_settings_enabled = enabled"
        tests = @($behaviour, $wiring)
        select = @("-k", "connection_fact_comes_from_the_capability or connection_controls_through_the_capability")
    },
    @{
        name = "M15 a refused Paper capability keeps the control checked"
        file = $orchestrator
        find = "        if accepted:\r?\n            return\r?\n        self\._page\.set_paper_order_capability\(False, emit_change=False\)"
        repl = "        return"
        tests = @($behaviour, $wiring)
        select = @("-k", "refusing_the_paper_capability or paper_capability_refusal")
    },
    @{
        name = "M16 a refused extended-hours toggle keeps the control checked"
        file = $orchestrator
        find = "        if accepted:\r?\n            return\r?\n        self\._page\.set_extended_hours_paper\(False, emit_change=False\)"
        repl = "        return"
        tests = @($behaviour, $wiring)
        select = @("-k", "refusing_extended_hours or extended_hours_refusal")
    },
    @{
        name = "M17 the window renders the Settings page again"
        file = $desktopPath
        find = "        self\.settings_orchestrator\.render_current\(\)\r?\n        self\.system_page = SystemPage\("
        repl = "        self.settings_orchestrator.render_current()`n        self.settings_page.render(None)`n        self.system_page = SystemPage("
        tests = @($architecture)
        select = @("-k", "never_renders_the_settings_page or only_caller_of_the_settings_render")
    },
    @{
        name = "M18 the window saves credentials itself again"
        file = $desktopPath
        find = "        page\.save_credentials_requested\.connect\(\r?\n            orchestrator\.save_credentials\r?\n        \)"
        repl = "        page.save_credentials_requested.connect(`n            lambda draft: self.credential_service.save_provider(draft.provider)`n        )"
        tests = @($architecture)
        select = @("-k", "window_calls_no_settings_service")
    },
    @{
        name = "M19 the capability imports MarketOrchestrator"
        file = $orchestrator
        find = "from us_quant\.desktop_credentials import DesktopCredentialService"
        repl = "from us_quant.desktop_credentials import DesktopCredentialService`nfrom us_quant.desktop_v2.orchestration.market import MarketOrchestrator"
        tests = @($architecture)
        select = @("-k", "imports_no_other_capability")
    },
    @{
        name = "M20 the capability drives the market switch itself"
        file = $orchestrator
        find = "        self\.market_switch_requested\.emit\(\r?\n            commit\.preferences\.market_provider\r?\n        \)"
        repl = "        self._request_market_switch(commit.preferences.market_provider)"
        tests = @($architecture, $behaviour)
        select = @("-k", "switch_interlocks_do_not_enter_settings or successful_provider_switch_adopts")
    },
    @{
        name = "M21 the window re-runs the transaction after adopting the commit"
        file = $desktopPath
        find = "        saved = commit\.preferences\r?\n        self\.config = commit\.config"
        repl = "        saved = commit.preferences`n        self.settings_service.commit(`n            saved,`n            current_config=self.config,`n            broker_config=self.broker_account,`n            runtime_guards=(self.market_data,),`n        )`n        self.config = commit.config"
        tests = @($architecture)
        select = @("-k", "window_keeps_the_global_composition")
    },
    @{
        name = "M22 the window imports a retired settings failure type again"
        file = $desktopPath
        find = "from us_quant\.user_settings import \(\r?\n    UserPreferences,\r?\n    UserPreferencesStore,\r?\n\)"
        repl = "from us_quant.user_settings import (`n    UserPreferences,`n    UserPreferencesStore,`n    UserSettingsError,`n)"
        tests = @($architecture)
        select = @("-k", "imports_no_retired_settings_failure_type")
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
