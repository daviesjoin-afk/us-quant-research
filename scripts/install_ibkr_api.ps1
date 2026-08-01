param(
    [string]$PythonClientPath = "C:\TWS API\source\pythonclient",
    [string]$PythonExecutable = "runtime\python313\python.exe",
    [string]$VirtualEnvironment = ".venv313"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedClientPath = Resolve-Path -LiteralPath $PythonClientPath

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $PythonExecutable)) {
        throw "Compatible Python not found at $PythonExecutable. Install Python 3.13 into runtime\python313 first."
    }

    $venvPython = Join-Path $VirtualEnvironment "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $PythonExecutable -m venv $VirtualEnvironment
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
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
}
finally {
    Pop-Location
}
