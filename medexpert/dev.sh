#!/usr/bin/env bash
# MedExpert — One-command dev setup
# Usage: bash medexpert/dev.sh [--no-frontend] [--skip-mcp] [--reset] [--help]
set -euo pipefail

# ── Flags ────────────────────────────────────────────────────────────────
NO_FRONTEND=false
SKIP_MCP=false
RESET=false

for arg in "$@"; do
  case "$arg" in
    --no-frontend) NO_FRONTEND=true ;;
    --skip-mcp)    SKIP_MCP=true ;;
    --reset)       RESET=true ;;
    --help|-h)
      echo "Usage: bash medexpert/dev.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --no-frontend  Skip frontend (Vite) dev server"
      echo "  --skip-mcp     Skip MCP server startup"
      echo "  --reset        Delete .venv, .env, stop Redis before setup"
      echo "  --help, -h     Show this help message"
      exit 0
      ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ── Output helpers ───────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── Resolve project root (one level above medexpert/) ────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MEDEXPERT_DIR="$SCRIPT_DIR"
cd "$PROJECT_ROOT"
info "Project root: $PROJECT_ROOT"

# ── Collect PIDs for cleanup ─────────────────────────────────────────────
CHILD_PIDS=()

CLEANING_UP=false
cleanup() {
  if $CLEANING_UP; then return; fi
  CLEANING_UP=true
  echo ""
  info "Shutting down..."
  for pid in "${CHILD_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  ok "All processes stopped. Redis container preserved for next run."
}
trap cleanup EXIT INT TERM

# ── Reset mode ───────────────────────────────────────────────────────────
if $RESET; then
  warn "Reset mode: cleaning previous state..."
  # Kill any lingering sam processes that lock .venv files
  if command -v taskkill >/dev/null 2>&1; then
    taskkill //F //IM sam.exe 2>/dev/null || true
  else
    pkill -f "sam run" 2>/dev/null || true
  fi
  sleep 1
  rm -rf .venv
  rm -f "$MEDEXPERT_DIR/.env"
  docker rm -f medexpert-redis 2>/dev/null || true
  ok "Reset complete"
fi

###########################################################################
# Phase 1 — Prerequisites
###########################################################################
info "Phase 1: Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 \
  || fail "Python not found. Install Python 3.10+"

PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
PY_VERSION=$($PYTHON --version 2>&1 | sed 's/Python //' | cut -d. -f1,2)
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  fail "Python 3.10+ required, found $PY_VERSION"
fi
info "Python $PY_VERSION ($PYTHON)"

command -v uv >/dev/null 2>&1 \
  || fail "'uv' not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
ok "uv $(uv --version 2>&1 | head -1)"

command -v docker >/dev/null 2>&1 \
  || fail "Docker not found. Install Docker Desktop."
docker info >/dev/null 2>&1 \
  || fail "Docker daemon not running. Start Docker Desktop."
ok "Docker running"

if ! $NO_FRONTEND; then
  command -v node >/dev/null 2>&1 \
    || fail "Node.js not found (needed for frontend). Use --no-frontend to skip."
  NODE_VERSION=$(node --version)
  ok "Node $NODE_VERSION"
  # Frontend package.json requires node >=25.5.0; we use --ignore-engines to work
  # with common LTS versions, but warn so users know about potential issues.
  NODE_MAJOR=$(echo "$NODE_VERSION" | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_MAJOR" -lt 20 ]; then
    warn "Node.js 20+ recommended. Frontend may have issues with $NODE_VERSION"
  fi
fi

ok "Prerequisites satisfied"

###########################################################################
# Phase 2 — Python Environment
###########################################################################
info "Phase 2: Setting up Python environment..."

if [ ! -d ".venv" ]; then
  uv venv --python 3.12 2>/dev/null || uv venv
  ok "Virtual environment created"
else
  ok "Virtual environment exists"
fi

# Activate venv (cross-platform)
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
  source .venv/Scripts/activate
else
  fail "Cannot find venv activate script"
fi
ok "Venv activated"

# Create placeholder static dirs so SAM_SKIP_UI_BUILD passes
mkdir -p config_portal/frontend/static
mkdir -p client/webui/frontend/static
mkdir -p docs/build

# Install SAM with build hook bypass
info "Installing solace-agent-mesh (this may take a minute)..."
SAM_SKIP_UI_BUILD=true uv sync --frozen --no-dev 2>/dev/null \
  || SAM_SKIP_UI_BUILD=true uv sync --no-dev
ok "solace-agent-mesh installed"

# Install medexpert-specific deps (avoids version conflict)
info "Installing MedExpert dependencies..."
uv pip install fastmcp httpx biopython lxml "redis[hiredis]" 2>/dev/null \
  || uv pip install fastmcp httpx biopython lxml redis
ok "MedExpert dependencies installed"

###########################################################################
# Phase 3 — Environment File
###########################################################################
info "Phase 3: Setting up environment file..."

ENV_FILE="$MEDEXPERT_DIR/.env"
ENV_EXAMPLE="$MEDEXPERT_DIR/.env.example"
if [ ! -f "$ENV_FILE" ]; then
  [ -f "$ENV_EXAMPLE" ] || fail "Missing $ENV_EXAMPLE — is this a complete clone?"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  # Generate a random session secret
  SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null \
    || python -c "import secrets; print(secrets.token_urlsafe(32))")
  sed -i "s|SESSION_SECRET_KEY=change-me-in-production|SESSION_SECRET_KEY=$SECRET|" "$ENV_FILE" 2>/dev/null \
    || sed -i '' "s|SESSION_SECRET_KEY=change-me-in-production|SESSION_SECRET_KEY=$SECRET|" "$ENV_FILE"
  # Add MEDEXPERT_ENV
  echo "MEDEXPERT_ENV=development" >> "$ENV_FILE"
  ok "Created .env from .env.example"
else
  ok ".env already exists"
fi

# Source env file
set -a
source "$ENV_FILE"
set +a

# Check for LLM API key
HAS_KEY=false
[ -n "${OPENAI_API_KEY:-}" ] && HAS_KEY=true
[ -n "${GEMINI_API_KEY:-}" ] && HAS_KEY=true
[ -n "${ANTHROPIC_API_KEY:-}" ] && HAS_KEY=true

if ! $HAS_KEY; then
  warn "No LLM API key found (OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY)"
  if [ -t 0 ]; then
    # Interactive terminal — prompt for key
    echo ""
    echo -n "Enter your OpenAI API key (or press Enter to skip): "
    read -r API_KEY || API_KEY=""
    if [ -n "$API_KEY" ]; then
      echo "OPENAI_API_KEY=$API_KEY" >> "$ENV_FILE"
      export OPENAI_API_KEY="$API_KEY"
      ok "API key saved to .env"
    else
      warn "Skipped — agents will fail without an LLM key"
    fi
  else
    warn "Non-interactive shell — set OPENAI_API_KEY in medexpert/.env before running"
  fi
fi

###########################################################################
# Phase 4 — Redis
###########################################################################
info "Phase 4: Starting Redis..."

if docker ps --format '{{.Names}}' | grep -q '^medexpert-redis$'; then
  ok "Redis container already running"
elif docker ps -a --format '{{.Names}}' | grep -q '^medexpert-redis$'; then
  docker start medexpert-redis >/dev/null
  ok "Redis container restarted"
else
  docker run -d --name medexpert-redis -p 6379:6379 redis:7-alpine >/dev/null
  ok "Redis container created"
fi

# Health check
info "Waiting for Redis..."
for i in $(seq 1 10); do
  if docker exec medexpert-redis redis-cli ping 2>/dev/null | grep -q PONG; then
    ok "Redis is ready"
    break
  fi
  if [ "$i" -eq 10 ]; then
    fail "Redis did not become ready in 10s"
  fi
  sleep 1
done

###########################################################################
# Phase 5 — MCP Servers
###########################################################################
if ! $SKIP_MCP; then
  info "Phase 5: Starting MCP servers..."

  mkdir -p "$MEDEXPERT_DIR/logs"
  cd "$MEDEXPERT_DIR/src"

  MCP_SERVERS=(
    pubmed clinicaltrials openfda cdc regulatory
    seer genomic environmental census_sdoh provider_intel
    imaging pharmacovigilance
    knowledge_graph societies vigibase
  )

  for srv in "${MCP_SERVERS[@]}"; do
    python -m "mcp_servers.${srv}.server" \
      > "$MEDEXPERT_DIR/logs/mcp-${srv}.log" 2>&1 &
    CHILD_PIDS+=($!)
  done
  ok "Started ${#MCP_SERVERS[@]} MCP servers (logs in medexpert/logs/)"

  # Health check Tier 1 ports
  info "Waiting for Tier 1 MCP servers (ports 9001-9005)..."
  MCP_READY=true
  for port in 9001 9002 9003 9004 9005; do
    PORT_UP=false
    for i in $(seq 1 15); do
      # --max-time 2: SSE endpoints hold connections open, so a timeout (exit 28)
      # means the server IS responding. Exit 0 or 28 = server is up.
      EXIT_CODE=$(curl -sf --max-time 2 "http://localhost:$port/sse" >/dev/null 2>&1; echo $?)
      if [ "$EXIT_CODE" -eq 0 ] || [ "$EXIT_CODE" -eq 28 ]; then
        PORT_UP=true
        break
      fi
      sleep 1
    done
    if $PORT_UP; then
      ok "MCP port $port ready"
    else
      warn "MCP port $port did not respond in 15s"
      MCP_READY=false
    fi
  done
  if $MCP_READY; then
    ok "All Tier 1 MCP servers ready"
  else
    warn "Some MCP servers may still be starting — check medexpert/logs/"
  fi

  cd "$PROJECT_ROOT"
else
  warn "Phase 5: Skipping MCP servers (--skip-mcp)"
fi

###########################################################################
# Phase 6 — Agents + Gateway (single sam run = shared DevBroker)
###########################################################################
info "Phase 6: Starting agents + gateway (single process)..."

cd "$MEDEXPERT_DIR"

CONFIGS_DIR="$MEDEXPERT_DIR/configs"

# Specialists first (for discovery ordering), then orchestrator, gateway.
# Note: feedback_bridge.yaml is excluded in dev mode — it uses a raw broker_input
# component that tries to connect to ws://localhost:8008, which doesn't exist when
# SOLACE_DEV_MODE=true (agents use an in-memory DevBroker instead).
sam run \
  "$CONFIGS_DIR/agents/literature_specialist.yaml" \
  "$CONFIGS_DIR/agents/clinical_trials_specialist.yaml" \
  "$CONFIGS_DIR/agents/drug_specialist.yaml" \
  "$CONFIGS_DIR/agents/regulatory_specialist.yaml" \
  "$CONFIGS_DIR/agents/epidemiology_specialist.yaml" \
  "$CONFIGS_DIR/agents/genomics_specialist.yaml" \
  "$CONFIGS_DIR/agents/environmental_specialist.yaml" \
  "$CONFIGS_DIR/agents/provider_intel_specialist.yaml" \
  "$CONFIGS_DIR/agents/verifier.yaml" \
  "$CONFIGS_DIR/agents/reviser.yaml" \
  "$CONFIGS_DIR/agents/orchestrator.yaml" \
  "$CONFIGS_DIR/gateways/webui.yaml" &
SAM_PID=$!
CHILD_PIDS+=($SAM_PID)

# Poll gateway readiness (cold start with 13 apps can take ~60s)
info "Waiting for gateway (http://localhost:${FASTAPI_PORT:-8000})..."
for i in $(seq 1 60); do
  if curl -sf "http://localhost:${FASTAPI_PORT:-8000}" >/dev/null 2>&1; then
    ok "Gateway is ready"
    break
  fi
  if [ "$i" -eq 60 ]; then
    warn "Gateway did not respond in 60s — it may still be starting"
  fi
  sleep 1
done

cd "$PROJECT_ROOT"

###########################################################################
# Phase 7 — Frontend
###########################################################################
if ! $NO_FRONTEND; then
  info "Phase 7: Starting frontend dev server..."

  cd client/webui/frontend

  if [ ! -d "node_modules" ]; then
    info "Installing frontend dependencies..."
    npm install --ignore-engines
  fi

  npm run dev &
  CHILD_PIDS+=($!)
  ok "Vite dev server starting on http://localhost:3000"

  cd "$PROJECT_ROOT"
else
  warn "Phase 7: Skipping frontend (--no-frontend)"
fi

###########################################################################
# Phase 8 — Status Banner
###########################################################################
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  MedExpert is running!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  Gateway API:  ${CYAN}http://localhost:${FASTAPI_PORT:-8000}${NC}"
if ! $NO_FRONTEND; then
  echo -e "  Frontend UI:  ${CYAN}http://localhost:3000${NC}"
fi
if ! $SKIP_MCP; then
  echo -e "  MCP Servers:  ${CYAN}ports 9001-9015${NC} (logs in medexpert/logs/)"
fi
echo -e "  Redis:        ${CYAN}localhost:6379${NC}"
echo ""
echo -e "  Press ${YELLOW}Ctrl+C${NC} to stop all processes."
echo ""

# Wait for the sam process (keeps script alive)
wait $SAM_PID
