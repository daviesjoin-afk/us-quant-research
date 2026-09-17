param(
    [string]$PythonClientPath = "C:\TWS API\source\pythonclient",
    [string]$VirtualEnvironment = ".venv"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path -LiteralPath $PythonClientPath)) {
    throw "Official IBKR Python client source was not found at '$PythonClientPath'. Install the TWS API or pass -PythonClientPath explicitly."
}
$resolvedClientPath = (Resolve-Path -LiteralPath $PythonClientPath).Path

Push-Location $projectRoot
try {
    $venvPython = Join-Path $VirtualEnvironment "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $legacyPython = Join-Path ".venv313" "Scripts\python.exe"
        if ($VirtualEnvironment -eq ".venv" -and (Test-Path -LiteralPath $legacyPython)) {
            Write-Warning "Using legacy .venv313. New environments should use .venv."
            $venvPython = $legacyPython
        }
        else {
            throw "Managed environment not found. Run scripts\bootstrap_windows.ps1 first."
        }
    }

    & $venvPython -m pip install $resolvedClientPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $venvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $venvPython -c "import ibapi; print('Official IBKR Python API import succeeded')"
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host "IBKR Python API installed into $venvPython" -ForegroundColor Green
}
finally {
    Pop-Location
}
