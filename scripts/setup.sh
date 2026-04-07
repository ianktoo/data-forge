#!/usr/bin/env bash
# DataForge environment bootstrap (Unix / macOS)
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${CYAN}[setup]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

# ── 1. uv ────────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi
ok "uv $(uv --version)"

# ── 2. Virtual environment + dependencies ─────────────────────────────────────
info "Syncing dependencies..."
uv sync
ok "Dependencies installed"

# ── 3. .env ───────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env
    info ".env created from template — add your API keys before running"
fi

# ── 4. Output directories ─────────────────────────────────────────────────────
mkdir -p output/sessions logs
ok "Directories ready"

echo ""
ok "Setup complete. Run: ./run.sh"
