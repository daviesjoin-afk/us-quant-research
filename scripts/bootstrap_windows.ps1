param(
    [string]$PythonVersion = "3.13",
    [string]$VirtualEnvironment = ".venv",
    [switch]$CoreOnly
)

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
        throw "Command failed with exit code $LASTEXITCODE: $Executable $($CommandArguments -join ' ')"
    }
}

try {
    $venvPython = Join-Path $VirtualEnvironment "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($null -ne $pyLauncher) {
            & py "-$PythonVersion" -m venv $VirtualEnvironment
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to create $VirtualEnvironment with Python $PythonVersion. Install Python $PythonVersion first."
            }
        }
        else {
            $python = Get-Command python -ErrorAction SilentlyContinue
            if ($null -eq $python) {
                throw "Python was not found. Install Python 3.12 or 3.13, then rerun this script."
            }
            & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 2)"
            if ($LASTEXITCODE -ne 0) {
                throw "The system Python is older than 3.12. Install Python 3.12 or 3.13."
            }
            & python -m venv $VirtualEnvironment
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to create $VirtualEnvironment with the system Python."
            }
        }
    }

    Invoke-Checked -Executable $venvPython -CommandArguments @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
    if ($CoreOnly) {
        Invoke-Checked -Executable $venvPython -CommandArguments @("-m", "pip", "install", "-e", ".[test]")
    }
    else {
        Invoke-Checked -Executable $venvPython -CommandArguments @("-m", "pip", "install", "-e", ".[desktop,test]")
    }

    Invoke-Checked -Executable $venvPython -CommandArguments @("-m", "us_quant", "doctor")
    Invoke-Checked -Executable $venvPython -CommandArguments @("-m", "pytest", "-q")
    Invoke-Checked -Executable $venvPython -CommandArguments @("-m", "compileall", "-q", "src", "tests")

    if (-not $CoreOnly) {
        $previousSelfTest = $env:US_QUANT_SELF_TEST
        $previousQtPlatform = $env:QT_QPA_PLATFORM
        try {
            $env:US_QUANT_SELF_TEST = "1"
            $env:QT_QPA_PLATFORM = "offscreen"
            Invoke-Checked -Executable $venvPython -CommandArguments @("desktop_main.py")
        }
        finally {
            $env:US_QUANT_SELF_TEST = $previousSelfTest
            $env:QT_QPA_PLATFORM = $previousQtPlatform
        }
    }

    Write-Host ""
    Write-Host "Bootstrap complete." -ForegroundColor Green
    Write-Host "Launch the desktop with: .\启动美股量化研究台.cmd"
    Write-Host "IBKR API support is optional and installed separately with scripts\install_ibkr_api.ps1."
}
finally {
    Pop-Location
}
