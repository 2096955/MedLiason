#!/usr/bin/env bash
# Usage: ./scripts/set-env.sh [development|staging|production]
ENV="${1:-development}"
if [ ! -f ".env.${ENV}" ]; then
    echo "Error: .env.${ENV} not found"
    exit 1
fi
cp ".env.${ENV}" .env
echo "Environment set to: ${ENV}"
cat .env
