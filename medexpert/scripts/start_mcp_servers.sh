#!/usr/bin/env bash
# Start all 18 MCP servers with health check polling
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$PROJECT_DIR/src"

cd "$SRC_DIR"

echo "Starting MCP servers..."

PIDS=()

# Tier 1
python -m mcp_servers.pubmed.server &
PIDS+=($!)
python -m mcp_servers.clinicaltrials.server &
PIDS+=($!)
python -m mcp_servers.openfda.server &
PIDS+=($!)
python -m mcp_servers.cdc.server &
PIDS+=($!)
python -m mcp_servers.regulatory.server &
PIDS+=($!)

# Tier 2
python -m mcp_servers.seer.server &
PIDS+=($!)
python -m mcp_servers.genomic.server &
PIDS+=($!)
python -m mcp_servers.environmental.server &
PIDS+=($!)
python -m mcp_servers.census_sdoh.server &
PIDS+=($!)
python -m mcp_servers.provider_intel.server &
PIDS+=($!)

# Tier 3 (specialized)
python -m mcp_servers.imaging.server &
PIDS+=($!)
python -m mcp_servers.pharmacovigilance.server &
PIDS+=($!)

# Degraded
python -m mcp_servers.knowledge_graph.server &
PIDS+=($!)
python -m mcp_servers.societies.server &
PIDS+=($!)
python -m mcp_servers.vigibase.server &
PIDS+=($!)

# Firecrawl-backed (require FIRECRAWL_API_KEY)
python -m mcp_servers.nhs_111.server &
PIDS+=($!)
python -m mcp_servers.bma_library.server &
PIDS+=($!)

# OpenEvidence (require OE_AUTH_STATE_PATH)
python -m mcp_servers.openevidence.server &
PIDS+=($!)

echo "Started ${#PIDS[@]} MCP servers"

# Health check polling
echo "Waiting for servers to become ready..."
for port in 9001 9002 9003 9004 9005 9006 9007 9008 9009 9010 9011 9012 9013 9014 9015 9016 9017 9018; do
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:$port/sse" > /dev/null 2>&1; then
            echo "  Port $port ready"
            break
        fi
        sleep 1
    done
done

echo "All MCP servers started. PIDs: ${PIDS[*]}"

# Trap to clean up on exit
cleanup() {
    echo "Stopping MCP servers..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

# Wait for all background processes
wait
