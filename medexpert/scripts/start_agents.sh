#!/usr/bin/env bash
# Start all agent YAMLs via sam run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIGS_DIR="$PROJECT_DIR/configs"

cd "$PROJECT_DIR"

echo "Starting MedExpert agents..."

PIDS=()

# Specialists
for config in \
    agents/literature_specialist.yaml \
    agents/clinical_trials_specialist.yaml \
    agents/drug_specialist.yaml \
    agents/regulatory_specialist.yaml \
    agents/epidemiology_specialist.yaml \
    agents/genomics_specialist.yaml \
    agents/environmental_specialist.yaml \
    agents/provider_intel_specialist.yaml \
    agents/verifier.yaml \
    agents/reviser.yaml; do

    echo "  Starting $config..."
    sam run "$CONFIGS_DIR/$config" &
    PIDS+=($!)
    sleep 2  # Stagger startup
done

# Orchestrator (start after specialists for discovery)
echo "  Starting orchestrator..."
sam run "$CONFIGS_DIR/agents/orchestrator.yaml" &
PIDS+=($!)

# Triage agents
echo "  Starting triage intake..."
sam run "$CONFIGS_DIR/agents/triage_intake.yaml" &
PIDS+=($!)
sleep 2

echo "  Starting triage orchestrator..."
sam run "$CONFIGS_DIR/agents/triage_orchestrator.yaml" &
PIDS+=($!)
sleep 2

# Feedback bridge (learning loop integration)
echo "  Starting feedback bridge..."
sam run "$CONFIGS_DIR/feedback_bridge.yaml" &
PIDS+=($!)

# Gateway
echo "  Starting webui gateway..."
sam run "$CONFIGS_DIR/gateways/webui.yaml" &
PIDS+=($!)

echo "Started ${#PIDS[@]} agents. PIDs: ${PIDS[*]}"

cleanup() {
    echo "Stopping agents..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

wait
