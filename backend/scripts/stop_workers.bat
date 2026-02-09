@echo off
REM ============================================================================
REM Boeing Data Hub - Stop All Celery Workers
REM ============================================================================
REM This script stops all running Celery workers and beat scheduler.
REM ============================================================================

echo.
echo ============================================================
echo   Boeing Data Hub - Stopping All Celery Workers
echo ============================================================
echo.

REM Kill all celery processes
echo Stopping all Celery processes...
taskkill /F /IM celery.exe 2>nul
if %errorlevel% equ 0 (
    echo Celery processes terminated.
) else (
    echo No Celery processes found running.
)

echo.
echo ============================================================
echo   All workers stopped!
echo ============================================================
echo.
pause
