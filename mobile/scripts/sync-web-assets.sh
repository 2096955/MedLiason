#!/usr/bin/env bash
# Build frontend and copy to mobile/www/ for Mode B (bundled) builds.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOBILE_DIR="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="${MOBILE_DIR}/../client/webui/frontend"

echo "Building frontend..."
cd "$FRONTEND_DIR" && npm run build

echo "Copying static assets to mobile/www/..."
rm -rf "${MOBILE_DIR}/www"
cp -r "${FRONTEND_DIR}/static" "${MOBILE_DIR}/www"

echo "Done. Run 'npx cap sync' to update native projects."
