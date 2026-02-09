# ============================================================================
# Boeing Data Hub - Celery Workers Startup Script (PowerShell)
# ============================================================================
# This script starts all Celery workers in separate terminal windows.
# Each worker handles specific queues for better log visibility.
#
# Usage: Right-click and "Run with PowerShell" or run from terminal:
#        .\start_workers.ps1
# ============================================================================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Boeing Data Hub - Starting Celery Workers" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Change to backend directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Split-Path -Parent $scriptDir
Set-Location $backendDir

Write-Host "Working directory: $backendDir" -ForegroundColor Gray
Write-Host ""

# Start Extraction & Normalization Worker
Write-Host "[1/5] Starting Extraction/Normalization worker..." -ForegroundColor Yellow
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title EXTRACT-NORM Worker && celery -A celery_app worker --pool=solo -Q extraction,normalization -l info -n extract@%h"
Start-Sleep -Seconds 2

# Start Publishing Worker
Write-Host "[2/5] Starting Publishing worker..." -ForegroundColor Yellow
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title PUBLISH Worker && celery -A celery_app worker --pool=solo -Q publishing -l info -n publish@%h"
Start-Sleep -Seconds 2

# Start Sync Worker (Boeing + Shopify)
Write-Host "[3/5] Starting Sync worker (Boeing + Shopify)..." -ForegroundColor Yellow
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title SYNC Worker && celery -A celery_app worker --pool=solo -Q sync_boeing,sync_shopify --concurrency=1 -l info -n sync@%h"
Start-Sleep -Seconds 2

# Start Default Worker (dispatchers, batch tasks)
Write-Host "[4/5] Starting Default worker (dispatchers)..." -ForegroundColor Yellow
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title DEFAULT Worker && celery -A celery_app worker --pool=solo -Q default -l info -n default@%h"
Start-Sleep -Seconds 2

# Start Celery Beat (scheduler)
Write-Host "[5/5] Starting Celery Beat scheduler..." -ForegroundColor Yellow
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "title CELERY BEAT && celery -A celery_app beat -l info"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  All workers started in separate windows!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Window Names:" -ForegroundColor White
Write-Host "  - EXTRACT-NORM Worker : Handles extraction, normalization" -ForegroundColor Gray
Write-Host "  - PUBLISH Worker      : Handles Shopify publishing" -ForegroundColor Gray
Write-Host "  - SYNC Worker         : Handles Boeing/Shopify sync" -ForegroundColor Gray
Write-Host "  - DEFAULT Worker      : Handles dispatchers, batch tasks" -ForegroundColor Gray
Write-Host "  - CELERY BEAT         : Scheduler for periodic tasks" -ForegroundColor Gray
Write-Host ""
Write-Host "  To stop all workers, close each terminal window." -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
