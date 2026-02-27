#!/usr/bin/env bash
set -euo pipefail

cd /app

# Validate required configs exist before starting
for cfg in configs/agents/triage_orchestrator.yaml \
           configs/agents/triage_intake.yaml \
           configs/gateways/webui.yaml; do
  if [ ! -s "$cfg" ]; then
    echo "[MedLiaison] FATAL: Required config not found or empty: $cfg"
    exit 1
  fi
done

echo "[MedLiaison] Starting -- triage pipeline, single process, dev_mode broker..."
echo "[MedLiaison] Host: 0.0.0.0, Port: ${PORT:-8080}"

export FASTAPI_HOST="0.0.0.0"
export FASTAPI_PORT="${PORT:-8080}"

exec sam run \
  configs/agents/triage_orchestrator.yaml \
  configs/agents/triage_intake.yaml \
  configs/gateways/webui.yaml
