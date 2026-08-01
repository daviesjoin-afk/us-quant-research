param(
    [string]$Python = ".\.venv313\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = (Resolve-Path (Join-Path $ProjectRoot $Python)).Path
$DistributionRoot = Join-Path $ProjectRoot "dist"
$ApplicationDirectory = Join-Path $DistributionRoot "USQuantResearch"
$ArchivePath = Join-Path $DistributionRoot "USQuantResearch-win64.zip"

Push-Location $ProjectRoot
try {
    $env:PYTHONPATH = Join-Path $ProjectRoot "src"
    & $PythonPath -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name "USQuantResearch" `
        --version-file "scripts\windows_version_info.txt" `
        --contents-directory "." `
        --paths "src" `
        --collect-submodules "ibapi" `
        --hidden-import "google.protobuf" `
        --add-data "configs;configs" `
        --add-data "data\reference;data\reference" `
        --add-data "data\normalized\ibkr\daily;data\normalized\ibkr\daily" `
        --add-data "research\results;research\results" `
        "desktop_main.py"
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
        "USQuantResearch 0.19.0"
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
    Write-Output "SHA256: $ArchiveHash"
}
finally {
    Pop-Location
}
