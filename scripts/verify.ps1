[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$CoreOnly,
    [string[]]$TestPath = @(),
    [int]$Workers = 0
)

# Three tiers, one contract: **no argument combination can make a gate weaker
# than it was before this parameter existed.**
#
#   .\scripts\verify.ps1
#       Full gate -- what a PR submission runs:
#           full pytest, doctor, compileall, PySide import, offscreen self-test.
#       Unchanged from before this file gained parameters.
#
#   .\scripts\verify.ps1 -CoreOnly
#       Core full -- a stage checkpoint:
#           full pytest, doctor, compileall.  No desktop self-test.
#       Also unchanged.  ``-CoreOnly`` does **not** mean "run fewer tests"; it
#       means "do not start Qt".  Focused selection is a separate parameter.
#
#   .\scripts\verify.ps1 -CoreOnly -TestPath @("tests/a.py", "tests/b.py")
#       Focused -- the development inner loop:
#           the named pytest files, doctor, compileall.  No desktop self-test.
#       The named files are *appended* to the pytest arguments, so this collects
#       exactly those files and nothing else.
#
# ``doctor`` and ``compileall`` stay in every tier on purpose.  Measured on this
# repository they cost about 0.3 s each and together catch import breakage,
# syntax breakage and repository-configuration breakage -- which is precisely the
# class of failure a focused run would otherwise miss until the full gate.
#
# ``-Workers``:
#     0 (default) -> sequential.  Deliberately **not** ``-n auto``: this suite
#                    builds Qt applications, ``MainWindow`` instances, SQLite
#                    files and temp trees, and an unbounded worker count is how a
#                    runner gets its CPU and RAM exhausted.  A focused run is a
#                    few dozen tests, where worker startup costs more than it
#                    saves, so sequential is also the right default there.
#     N > 0       -> ``-n N --dist=loadscope``.  ``loadscope`` keeps a
#                    module's (or class's) tests on one worker, which is what
#                    keeps module-level fixtures, Qt global state and
#                    per-module temp directories working.
#
# There is deliberately **no** changed-file-to-test mapping here.  Guessing which
# tests a diff affects needs a dependency graph that this repository's fixtures
# are not stable enough to justify maintaining, and a wrong guess is a silent
# hole in a gate.  Each task instruction names its focused set explicitly.
#
# One calling convention to know: pass ``-TestPath`` as a real PowerShell array,
# by invoking the script **directly** --
#
#     .\scripts\verify.ps1 -TestPath @("tests/a.py", "tests/b.py")
#
# Do **not** use ``pwsh -File scripts\verify.ps1 -TestPath a b``.  ``-File``
# hands every argument over as a literal string, so ``b`` would bind positionally
# to ``-Workers`` and produce a confusing "cannot convert to Int32" instead of
# running the two files.  ``PositionalBinding = $false`` below makes that misuse
# fail loudly rather than silently, but it cannot make ``-File`` carry an array.
# Non-PowerShell callers should run the pytest command itself, which is what CI
# does.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$CommandArguments
    )
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Name)
    Write-Host ""
    Write-Host "== $Name" -ForegroundColor Cyan
}

try {
    $env:PYTHONPATH = "src"
    $managedPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $legacyPython = Join-Path $projectRoot ".venv313\Scripts\python.exe"
    $python = if (Test-Path -LiteralPath $managedPython) {
        $managedPython
    }
    elseif (Test-Path -LiteralPath $legacyPython) {
        $legacyPython
    }
    else {
        "python"
    }

    # Focused selection is the *only* thing that narrows the pytest arguments.
    # ``$TestPath`` empty means every test, in both the full gate and -CoreOnly.
    $focused = $TestPath.Count -gt 0

    Invoke-Checked -Executable $python -CommandArguments @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 2)")

    $pytestArgs = @("-m", "pytest", "-q")
    if ($Workers -gt 0) {
        $pytestArgs += @("-n", "$Workers", "--dist=loadscope")
    }
    if ($focused) {
        $pytestArgs += $TestPath
    }

    if ($focused) {
        Write-Step "pytest (focused: $($TestPath.Count) path(s))"
    }
    else {
        Write-Step "pytest (all tests)"
    }
    Invoke-Checked -Executable $python -CommandArguments $pytestArgs

    Write-Step "doctor"
    Invoke-Checked -Executable $python -CommandArguments @("-m", "us_quant", "doctor")

    Write-Step "compileall"
    Invoke-Checked -Executable $python -CommandArguments @("-m", "compileall", "-q", "src", "tests")

    # The desktop self-test is the full gate's alone: it starts a real Qt
    # application and exercises desktop composition.  Neither a focused run nor a
    # pure-service -CoreOnly run should pay for that.
    if (-not $CoreOnly -and -not $focused) {
        Write-Step "desktop offscreen self-test"
        Invoke-Checked -Executable $python -CommandArguments @("-c", "import PySide6")
        $previousSelfTest = $env:US_QUANT_SELF_TEST
        $previousQtPlatform = $env:QT_QPA_PLATFORM
        try {
            $env:US_QUANT_SELF_TEST = "1"
            $env:QT_QPA_PLATFORM = "offscreen"
            Invoke-Checked -Executable $python -CommandArguments @("desktop_main.py")
        }
        finally {
            $env:US_QUANT_SELF_TEST = $previousSelfTest
            $env:QT_QPA_PLATFORM = $previousQtPlatform
        }
    }
}
finally {
    Pop-Location
}
