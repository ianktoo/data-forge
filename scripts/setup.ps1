# DataForge environment bootstrap (PowerShell)
$ErrorActionPreference = "Stop"

function info($msg) { Write-Host "[setup] $msg" -ForegroundColor Cyan }
function ok($msg)   { Write-Host "[ok]    $msg" -ForegroundColor Green }
function err($msg)  { Write-Host "[error] $msg" -ForegroundColor Red; exit 1 }

# ── 1. uv ────────────────────────────────────────────────────────────────────
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    info "Installing uv..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}
ok "uv $(uv --version)"

# ── 2. Dependencies ───────────────────────────────────────────────────────────
info "Syncing dependencies..."
uv sync
ok "Dependencies installed"

# ── 3. .env ───────────────────────────────────────────────────────────────────
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    info ".env created - add your API keys before running"
}

# ── 4. Directories ────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path output/sessions, logs | Out-Null
ok "Directories ready"

Write-Host ""
ok "Setup complete. Run: ./run.ps1"
