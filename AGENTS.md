# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This repo contains **Solace Agent Mesh (SAM)** (Python framework at root) and **MedExpert** (life-sciences app at `medexpert/`), plus three frontend packages (WebUI, Config Portal, Docs).

### System requirements

- **Python 3.12** (used by `make dev-setup` via `uv venv --python 3.12`)
- **Node.js >=25.5.0 <26** (required by all three `package.json` engine fields). Install via `nvm install 25 && nvm alias default 25`.
- **uv** (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Playwright system deps: `sudo apt-get install -y libxslt1.1 libevent-2.1-7t64 libgstreamer-plugins-base1.0-0 libgstreamer-gl1.0-0 libgstreamer-plugins-bad1.0-0 libavif16 libharfbuzz-icu0 libwayland-server0 libmanette-0.2-0 libhyphen0 libwoff1`

### Python backend (SAM framework)

- **Dev setup**: `make dev-setup` (creates `.venv`, installs all deps + Playwright browsers)
- **Activate venv**: `source .venv/bin/activate`
- **Lint**: `uv run ruff check .` (pre-existing lint errors in repo; ruff itself works)
- **Format**: `uv run ruff format .`
- **Unit tests**: `uv run pytest tests/unit -v` (2158 pass, 1 pre-existing failure)
- **All tests**: `make test` (excludes stress/long_soak markers)

### Hatch build hook gotcha

`uv sync --all-extras` triggers a hatch build hook (`.github/helper_scripts/build_frontend.py`) that runs `npm ci && npm run build` for all three frontends. To skip this during dev setup, set `SAM_SKIP_UI_BUILD=true` and ensure placeholder directories exist: `mkdir -p config_portal/frontend/static client/webui/frontend/static docs/build`. Then build frontends separately.

### Frontend (WebUI)

- **Location**: `client/webui/frontend/`
- **Install**: `npm install` (uses `package-lock.json`)
- **Dev server**: `npm run dev` (Vite on port 3000)
- **Lint**: `npm run lint` (ESLint, 0 errors, warnings only)
- **Build**: `npm run build-package` (library build succeeds; TypeScript type emission has pre-existing TS errors)
- The WebUI requires a backend API at port 8000 for `/api/v1/config`; without it a config error page shows. A minimal FastAPI stub on port 8000 serving config JSON is sufficient to render the chat UI.

### Config Portal frontend

- **Location**: `config_portal/frontend/`
- **Install**: `npm install`
- **Dev server**: `npm run dev`

### Documentation site

- **Location**: `docs/`
- **Install**: `npm install`
- **Dev server**: `npm start` (Docusaurus on port 3000)

### MedExpert application

- **Location**: `medexpert/`
- Requires Redis, LLM API key (GEMINI_API_KEY), and `.env` file (copy from `.env.example`)
- Full stack start: `./scripts/start_all.sh`
- Tests: `pytest tests/unit -v`, `pytest tests/contract -v`
