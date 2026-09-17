param(
    [switch]$CoreOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
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

    Invoke-Checked $python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 2)"
    Invoke-Checked $python -m pytest -q
    Invoke-Checked $python -m us_quant doctor
    Invoke-Checked $python -m compileall -q src tests

    if (-not $CoreOnly) {
        Invoke-Checked $python -c "import PySide6"
        $previousSelfTest = $env:US_QUANT_SELF_TEST
        $previousQtPlatform = $env:QT_QPA_PLATFORM
        try {
            $env:US_QUANT_SELF_TEST = "1"
            $env:QT_QPA_PLATFORM = "offscreen"
            Invoke-Checked $python desktop_main.py
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
