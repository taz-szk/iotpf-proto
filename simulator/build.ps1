# IoT Device Simulator build script
# Usage: run from simulator\ folder:  .\build.ps1
$ErrorActionPreference = "Stop"

$SimDir  = $PSScriptRoot
$RootDir = Split-Path -Parent $SimDir
$DistExe = Join-Path $SimDir "dist\IoT_Simulator.exe"

Write-Host "=== IoT Device Simulator Build ===" -ForegroundColor Cyan

# 1. Install dependencies
Write-Host "`n[1/3] Installing dependencies..." -ForegroundColor Yellow
python -m pip install -e "$RootDir\sdk\python" --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "SDK install failed" -ForegroundColor Red; exit 1 }

python -m pip install -r "$SimDir\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "requirements install failed" -ForegroundColor Red; exit 1 }

python -m pip install pyinstaller --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "PyInstaller install failed" -ForegroundColor Red; exit 1 }

# 2. Build
Write-Host "`n[2/3] Building with PyInstaller..." -ForegroundColor Yellow
Set-Location $SimDir
python -m PyInstaller simulator.spec --noconfirm
if ($LASTEXITCODE -ne 0) { Write-Host "Build failed" -ForegroundColor Red; exit 1 }

# 3. Done
if (Test-Path $DistExe) {
    $size = [math]::Round((Get-Item $DistExe).Length / 1MB, 1)
    Write-Host "`n[3/3] Build complete!" -ForegroundColor Green
    Write-Host "  Output: $DistExe ($size MB)" -ForegroundColor Green
    Write-Host "`nDistribute IoT_Simulator.exe as a standalone file."
    Write-Host "certs/ and config.json are auto-created next to the exe."
} else {
    Write-Host "`nBuild failed: exe not found" -ForegroundColor Red
    exit 1
}
