$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

try {
    $env:PYTHONPATH = "src"
    $localPython = Join-Path $projectRoot ".venv313\Scripts\python.exe"
    $python = if (Test-Path -LiteralPath $localPython) {
        $localPython
    } else {
        "python"
    }

    & $python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $python -m us_quant doctor
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $python -m compileall -q src tests
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
