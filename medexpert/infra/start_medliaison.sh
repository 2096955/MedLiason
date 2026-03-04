#!/usr/bin/env bash
# Full-stack MedExpert startup for Cloud Run single-container deployment.
#
# Starts: Redis → 10 MCP servers → sam run (13 agents + feedback bridge + gateway)
#
# Cold store (SQLite) is stored on the container's ephemeral filesystem at
# /app/data/. Data is lost on container restart. Learning loops (specialist
# calibration, prompt evolution, feedback history) reset to defaults after
# each restart. For persistent learning, mount a Cloud Storage FUSE volume
# or migrate to Cloud SQL.
set -euo pipefail

cd /app

# ---------------------------------------------------------------------------
# 1. Validate all required configs
# ---------------------------------------------------------------------------
REQUIRED_CONFIGS=(
  configs/agents/orchestrator.yaml
  configs/agents/literature_specialist.yaml
  configs/agents/clinical_trials_specialist.yaml
  configs/agents/drug_specialist.yaml
  configs/agents/regulatory_specialist.yaml
  configs/agents/epidemiology_specialist.yaml
  configs/agents/genomics_specialist.yaml
  configs/agents/environmental_specialist.yaml
  configs/agents/provider_intel_specialist.yaml
  configs/agents/verifier.yaml
  configs/agents/reviser.yaml
  configs/agents/triage_intake.yaml
  configs/agents/triage_orchestrator.yaml
  # feedback_bridge.yaml REMOVED — blocks gateway startup due to hanging
  # broker connection. Re-add once the DevBroker connection issue is resolved.
  # webui.yaml MUST be last — uvicorn in the gateway keeps sam run alive.
  # All other configs must be loaded before the gateway initialises.
  configs/gateways/webui.yaml
)

for cfg in "${REQUIRED_CONFIGS[@]}"; do
  if [ ! -s "$cfg" ]; then
    echo "[MedLiaison] FATAL: Required config not found or empty: $cfg"
    exit 1
  fi
done

echo "[MedLiaison] Starting full-stack MedExpert..."
echo "[MedLiaison] Host: 0.0.0.0, Port: ${PORT:-8080}"

export FASTAPI_HOST="0.0.0.0"
export FASTAPI_PORT="${PORT:-8080}"

# ---------------------------------------------------------------------------
# 2. Start Redis (ephemeral, localhost only)
# ---------------------------------------------------------------------------
echo "[MedLiaison] Starting Redis..."
redis-server --daemonize yes --bind 127.0.0.1 --port 6379

for i in $(seq 1 10); do
  redis-cli ping 2>/dev/null | grep -q PONG && break
  sleep 1
done

if ! redis-cli ping 2>/dev/null | grep -q PONG; then
  echo "[MedLiaison] FATAL: Redis failed to start"
  exit 1
fi
echo "[MedLiaison] Redis ready"

# ---------------------------------------------------------------------------
# 3. Start MCP servers (background processes)
# ---------------------------------------------------------------------------
MCP_PIDS=()
MCP_SERVERS=(
  "mcp_servers.pubmed.server:9001"
  "mcp_servers.clinicaltrials.server:9002"
  "mcp_servers.openfda.server:9003"
  "mcp_servers.cdc.server:9004"
  "mcp_servers.regulatory.server:9005"
  "mcp_servers.seer.server:9006"
  "mcp_servers.genomic.server:9007"
  "mcp_servers.environmental.server:9008"
  "mcp_servers.census_sdoh.server:9009"
  "mcp_servers.provider_intel.server:9010"
  "mcp_servers.knowledge_graph.server:9011"
  "mcp_servers.nhs_111.server:9016"
  "mcp_servers.bma_library.server:9017"
  "mcp_servers.openevidence.server:9018"
)

for entry in "${MCP_SERVERS[@]}"; do
  module="${entry%%:*}"
  port="${entry##*:}"
  echo "[MedLiaison] Starting MCP: $module (port $port)"
  python -m "$module" &
  MCP_PIDS+=($!)
done

# ---------------------------------------------------------------------------
# 4. Health check MCP servers (TCP socket, not /sse — SSE streams hang)
# ---------------------------------------------------------------------------
echo "[MedLiaison] Waiting for MCP servers..."
for entry in "${MCP_SERVERS[@]}"; do
  port="${entry##*:}"
  module="${entry%%:*}"
  ready=false
  for i in $(seq 1 30); do
    if python -c "import socket; s=socket.create_connection(('localhost',$port),timeout=2); s.close()" 2>/dev/null; then
      ready=true
      break
    fi
    sleep 1
  done
  if [ "$ready" = true ]; then
    echo "[MedLiaison]   Port $port ready ($module)"
  else
    echo "[MedLiaison]   WARNING: Port $port not ready ($module) — continuing (graceful degradation)"
  fi
done

# ---------------------------------------------------------------------------
# 4b. Start Memgraph MCP server (streamable-http on port 8000)
# Connects to Memgraph sidecar at bolt://localhost:7687
# ---------------------------------------------------------------------------
echo "[MedLiaison] Starting Memgraph MCP server..."
MCP_TRANSPORT=streamable-http python -m mcp_memgraph &
MCP_PIDS+=($!)

# Health check Memgraph MCP (port 8000)
ready=false
for i in $(seq 1 30); do
  if python -c "import socket; s=socket.create_connection(('localhost',8000),timeout=2); s.close()" 2>/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" = true ]; then
  echo "[MedLiaison]   Port 8000 ready (memgraph-mcp)"
else
  echo "[MedLiaison]   WARNING: Port 8000 not ready (memgraph-mcp) — continuing (graceful degradation)"
fi

# ---------------------------------------------------------------------------
# 5. Graceful shutdown handler
# ---------------------------------------------------------------------------
cleanup() {
  echo "[MedLiaison] Shutting down..."
  # Kill SAM first — allow PERSIST step to flush session signals
  if [ -n "${SAM_PID:-}" ]; then
    kill "$SAM_PID" 2>/dev/null || true
    wait "$SAM_PID" 2>/dev/null || true
  fi
  # Kill MCP servers in reverse start order
  for (( i=${#MCP_PIDS[@]}-1; i>=0; i-- )); do
    kill "${MCP_PIDS[$i]}" 2>/dev/null || true
  done
  # Stop Redis last
  redis-cli shutdown nosave 2>/dev/null || true
  echo "[MedLiaison] Shutdown complete"
}
trap cleanup SIGTERM SIGINT EXIT

# ---------------------------------------------------------------------------
# 6. Start sam run (background, wait on PID)
# --system-env: skip .env file loading (SAM CLI flag, run_cmd.py:53-58).
# On Cloud Run, env vars are injected via --set-env-vars in the deploy
# command, not from a .env file. SAM inherits system env vars normally;
# --system-env prevents SAM from also loading a .env that may contain
# conflicting localhost settings (e.g. FASTAPI_HOST=localhost).
# ---------------------------------------------------------------------------
echo "[MedLiaison] Starting sam run with ${#REQUIRED_CONFIGS[@]} configs..."
sam run --system-env "${REQUIRED_CONFIGS[@]}" &
SAM_PID=$!
wait $SAM_PID
