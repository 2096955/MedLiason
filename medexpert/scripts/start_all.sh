#!/usr/bin/env bash
# Full startup sequence: Redis -> MCP servers -> Agents -> Gateway
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== MedExpert Full Stack Startup ==="

# 0. Validate configuration
echo "[0/3] Validating configuration..."
python -m lifesci_common.config_validator || exit 1

# 1. Check Redis
echo "[1/3] Checking Redis..."
if redis-cli ping > /dev/null 2>&1; then
    echo "  Redis is running"
else
    echo "  Starting Redis..."
    redis-server --daemonize yes
    sleep 2
fi

# 2. Start MCP servers
echo "[2/3] Starting MCP servers..."
"$SCRIPT_DIR/start_mcp_servers.sh" &
MCP_PID=$!
sleep 10  # Wait for MCP servers to initialize

# 3. Start agents
echo "[3/3] Starting agents..."
"$SCRIPT_DIR/start_agents.sh" &
AGENTS_PID=$!

echo ""
echo "=== MedExpert is starting up ==="
echo "  WebUI will be available at: http://localhost:8000"
echo "  MCP servers: ports 9001-9015"
echo "  Press Ctrl+C to stop all services"

cleanup() {
    echo ""
    echo "Shutting down MedExpert..."
    kill $AGENTS_PID 2>/dev/null || true
    kill $MCP_PID 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

wait
