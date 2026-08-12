# export-migration.ps1
# IoT Platform migration package creator (run on current PC)
# Usage: .\scripts\export-migration.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$outDir     = Join-Path $projectDir "iot-platform-migration"

Write-Host ""
Write-Host "===== IoT Platform Migration Export =====" -ForegroundColor Cyan
Write-Host "Output: $outDir"
Write-Host ""

if (Test-Path $outDir) {
    Write-Host "[!] Removing existing migration folder..."
    Remove-Item -Recurse -Force $outDir
}
New-Item -ItemType Directory -Path $outDir | Out-Null
New-Item -ItemType Directory -Path "$outDir\volumes" | Out-Null

# --- 1. Export Docker volumes ---
Write-Host "[1/5] Exporting Docker volumes..." -ForegroundColor Yellow

$volumes = @("postgres_data","influxdb_data","grafana_data","step_ca_data","emqx_data","minio_data")

foreach ($vol in $volumes) {
    $fullName = "iot-platform_$vol"
    Write-Host "  - $fullName ..."
    docker run --rm `
        -v "${fullName}:/data" `
        -v "$outDir\volumes:/backup" `
        alpine sh -c "tar czf /backup/$vol.tar.gz -C /data . 2>/dev/null"
    if ($LASTEXITCODE -eq 0) {
        $item = Get-Item "$outDir\volumes\$vol.tar.gz" -ErrorAction SilentlyContinue
        if ($item) {
            $mb = [math]::Round($item.Length / 1048576, 1)
            Write-Host "    OK ($mb MB)" -ForegroundColor Green
        } else {
            Write-Host "    OK" -ForegroundColor Green
        }
    } else {
        Write-Warning "    FAILED (continuing)"
    }
}

# --- 2. Copy TLS certs ---
Write-Host "[2/5] Copying TLS certificates..." -ForegroundColor Yellow
$certsDir = Join-Path $projectDir "certs"
if (Test-Path $certsDir) {
    Copy-Item -Recurse $certsDir "$outDir\certs"
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Warning "  certs/ not found"
}

# --- 3. Copy simulator device certs ---
Write-Host "[3/5] Copying simulator device certs..." -ForegroundColor Yellow
$simCertsDir = Join-Path $projectDir "simulator\certs"
if (Test-Path $simCertsDir) {
    Copy-Item -Recurse $simCertsDir "$outDir\simulator-certs"
    $count = (Get-ChildItem $simCertsDir -Recurse -File).Count
    Write-Host "  OK ($count files)" -ForegroundColor Green
} else {
    Write-Host "  No simulator certs found (skip)"
}

# --- 4. Copy .env ---
Write-Host "[4/5] Copying .env..." -ForegroundColor Yellow
$envFile = Join-Path $projectDir ".env"
if (Test-Path $envFile) {
    Copy-Item $envFile "$outDir\.env"
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Warning "  .env not found"
}

# --- 5. Zip codebase ---
Write-Host "[5/5] Zipping codebase..." -ForegroundColor Yellow
$zipPath = "$outDir\iot-platform-code.zip"
# Collect all items except the migration output folder and heavy build artifacts
$itemsToZip = Get-ChildItem -Path $projectDir -Force | Where-Object {
    $_.Name -notin @("iot-platform-migration", ".git") -and
    $_.Name -notmatch '^\.git$'
} | Select-Object -ExpandProperty FullName
Compress-Archive -Path $itemsToZip -DestinationPath $zipPath -Force
Write-Host "  OK" -ForegroundColor Green

# --- Done ---
Write-Host ""
Write-Host "===== Export Complete =====" -ForegroundColor Cyan
Write-Host "Package folder: $outDir"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Copy the 'iot-platform-migration' folder to the new PC (USB/share)"
Write-Host "  2. On the new PC:"
Write-Host "     a. Install Docker Desktop"
Write-Host "     b. Unzip iot-platform-code.zip to a folder"
Write-Host "     c. Run: .\scripts\import-migration.ps1 -MigrationDir <path\to\iot-platform-migration>"
Write-Host ""
Write-Host "Package contents:" -ForegroundColor Cyan
Get-ChildItem $outDir -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($outDir.Length + 1)
    $mb  = [math]::Round($_.Length / 1048576, 2)
    Write-Host ("  {0,-50} {1,6} MB" -f $rel, $mb)
}
