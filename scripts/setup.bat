@echo off
REM DataForge environment bootstrap (Windows CMD)
setlocal

echo [setup] Checking uv...
where uv >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [setup] Installing uv...
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)

echo [setup] Syncing dependencies...
uv sync
if %ERRORLEVEL% NEQ 0 ( echo [error] uv sync failed & exit /b 1 )

if not exist .env (
    copy .env.example .env >nul
    echo [setup] .env created - add your API keys before running
)

if not exist output\sessions mkdir output\sessions
if not exist logs mkdir logs

echo [ok] Setup complete. Run: run.bat
