@echo off
REM ============================================================================
REM Boeing Data Hub - Celery Workers Startup Script
REM ============================================================================
REM This script starts all Celery workers in separate terminal windows.
REM Each worker handles specific queues for better log visibility.
REM ============================================================================

echo.
echo ============================================================
echo   Boeing Data Hub - Starting Celery Workers
echo ============================================================
echo.

REM Change to backend directory
cd /d "%~dp0.."

REM Start Extraction & Normalization Worker
echo Starting Extraction/Normalization worker...
start "EXTRACT-NORM Worker" cmd /k "celery -A celery_app worker --pool=solo -Q extraction,normalization -l info -n extract@%%h"

REM Wait a moment to avoid Redis connection race
timeout /t 2 /nobreak > nul

REM Start Publishing Worker
echo Starting Publishing worker...
start "PUBLISH Worker" cmd /k "celery -A celery_app worker --pool=solo -Q publishing -l info -n publish@%%h"

timeout /t 2 /nobreak > nul

REM Start Sync Worker (Boeing + Shopify)
echo Starting Sync worker (Boeing + Shopify)...
start "SYNC Worker" cmd /k "celery -A celery_app worker --pool=solo -Q sync_boeing,sync_shopify --concurrency=1 -l info -n sync@%%h"

timeout /t 2 /nobreak > nul

REM Start Default Worker (dispatchers, batch tasks)
echo Starting Default worker (dispatchers)...
start "DEFAULT Worker" cmd /k "celery -A celery_app worker --pool=solo -Q default -l info -n default@%%h"

timeout /t 2 /nobreak > nul

REM Start Celery Beat (scheduler)
echo Starting Celery Beat scheduler...
start "CELERY BEAT" cmd /k "celery -A celery_app beat -l info"

echo.
echo ============================================================
echo   All workers started in separate windows!
echo ============================================================
echo.
echo   Window Names:
echo   - EXTRACT-NORM Worker : Handles extraction, normalization
echo   - PUBLISH Worker      : Handles Shopify publishing
echo   - SYNC Worker         : Handles Boeing/Shopify sync
echo   - DEFAULT Worker      : Handles dispatchers, batch tasks
echo   - CELERY BEAT         : Scheduler for periodic tasks
echo.
echo   Close this window or press any key to exit.
echo ============================================================
pause > nul
