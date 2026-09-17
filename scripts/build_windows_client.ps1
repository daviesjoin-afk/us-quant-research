param(
    [string]$Python = "",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($Python)) {
    $managedPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $legacyPython = Join-Path $ProjectRoot ".venv313\Scripts\python.exe"
    if (Test-Path -LiteralPath $managedPython) {
        $PythonPath = $managedPython
    }
    elseif (Test-Path -LiteralPath $legacyPython) {
        $PythonPath = $legacyPython
    }
    else {
        throw "No managed virtual environment was found. Run scripts\bootstrap_windows.ps1 first."
    }
}
else {
    $PythonPath = (Resolve-Path (Join-Path $ProjectRoot $Python)).Path
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $DistributionRoot = Join-Path $ProjectRoot "dist"
}
else {
    $DistributionRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $ProjectRoot $OutputRoot)
    )
}
$BuildRoot = Join-Path $DistributionRoot ".build"
$ApplicationDirectory = Join-Path $DistributionRoot "USQuantResearch"
$ArchivePath = Join-Path $DistributionRoot "USQuantResearch-win64.zip"

Push-Location $ProjectRoot
try {
    $env:PYTHONPATH = Join-Path $ProjectRoot "src"

    & $PythonPath -c "import PySide6; import ibapi"
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop packaging requires PySide6 and the official IBKR Python API. Run scripts\bootstrap_windows.ps1, then scripts\install_ibkr_api.ps1."
    }

    $Version = (& $PythonPath -c "from importlib.metadata import version; print(version('us-quant'))").Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Version)) {
        throw "Unable to read the installed us-quant package version."
    }

    $pyInstallerArgs = @(
        "--noconfirm",
        "--clean",
        "--distpath", $DistributionRoot,
        "--workpath", $BuildRoot,
        "--windowed",
        "--name", "USQuantResearch",
        "--version-file", "scripts\windows_version_info.txt",
        "--contents-directory", ".",
        "--paths", "src",
        "--collect-submodules", "ibapi",
        "--hidden-import", "google.protobuf"
    )

    $dataMappings = @(
        @{ Source = "configs"; Destination = "configs" },
        @{ Source = "data\reference"; Destination = "data\reference" },
        @{ Source = "data\normalized\ibkr\daily"; Destination = "data\normalized\ibkr\daily" },
        @{ Source = "research\results"; Destination = "research\results" }
    )
    foreach ($mapping in $dataMappings) {
        $sourcePath = Join-Path $ProjectRoot $mapping.Source
        if (Test-Path -LiteralPath $sourcePath) {
            $pyInstallerArgs += @("--add-data", "$($mapping.Source);$($mapping.Destination)")
        }
        else {
            Write-Warning "Optional build data not found; skipping: $($mapping.Source)"
        }
    }
    $pyInstallerArgs += "desktop_main.py"

    & $PythonPath -m PyInstaller @pyInstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }

    Copy-Item `
        -LiteralPath (Join-Path $ProjectRoot "docs\CLIENT_GUIDE.md") `
        -Destination (Join-Path $ApplicationDirectory "README.zh-CN.md") `
        -Force
    Copy-Item `
        -LiteralPath (Join-Path $ProjectRoot "docs\MATURE_QUANT_CASE_REVIEW.md") `
        -Destination (Join-Path $ApplicationDirectory "QUANT_CASE_REVIEW.zh-CN.md") `
        -Force
    Copy-Item `
        -LiteralPath (Join-Path $ProjectRoot "docs\PROJECT_STATUS_2026-07-25.md") `
        -Destination (Join-Path $ApplicationDirectory "PROJECT_STATUS.zh-CN.md") `
        -Force
    Copy-Item `
        -LiteralPath (Join-Path $ProjectRoot "docs\INTERNAL_REVIEW_2026-07-25.md") `
        -Destination (Join-Path $ApplicationDirectory "INTERNAL_REVIEW.zh-CN.md") `
        -Force
    Copy-Item `
        -LiteralPath (Join-Path $ProjectRoot "docs\ARCHITECTURE_REFACTOR_2026-08-09.md") `
        -Destination (Join-Path $ApplicationDirectory "ARCHITECTURE_REFACTOR.zh-CN.md") `
        -Force

    if (Test-Path -LiteralPath $ArchivePath) {
        Remove-Item -LiteralPath $ArchivePath -Force
    }
    Compress-Archive `
        -Path (Join-Path $ApplicationDirectory "*") `
        -DestinationPath $ArchivePath `
        -CompressionLevel Optimal

    $ArchiveHash = (Get-FileHash `
        -LiteralPath $ArchivePath `
        -Algorithm SHA256).Hash
    $ReleaseInfo = @(
        "USQuantResearch $Version"
        "BuiltAt=$([DateTime]::UtcNow.ToString('o'))"
        "Archive=$(Split-Path -Leaf $ArchivePath)"
        "SHA256=$ArchiveHash"
        "Signing=unsigned-local-build"
    )
    $ReleaseInfo | Set-Content `
        -LiteralPath (Join-Path $DistributionRoot "USQuantResearch-win64.sha256.txt") `
        -Encoding utf8

    Write-Output "Application: $ApplicationDirectory"
    Write-Output "Archive: $ArchivePath"
    Write-Output "Version: $Version"
    Write-Output "SHA256: $ArchiveHash"
}
finally {
    Pop-Location
}
