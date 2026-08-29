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
- The WebUI requires a backend API at port 8000 for `/api/v1/config`; without it a config error page shows.

### Config Portal frontend

- **Location**: `config_portal/frontend/`
- **Install**: `npm install`
- **Dev server**: `npm run dev`

### Documentation site

- **Location**: `docs/`
- **Install**: `npm install`
- **Dev server**: `npm start` (Docusaurus on port 3000)

### Editable install symlinks

The hatch wheel build uses `force-include` to map `cli/`, `templates/`, and `evaluation/` into the `solace_agent_mesh` package. For editable installs, these don't exist inside `src/solace_agent_mesh/`. Create symlinks:
```
cd src/solace_agent_mesh && ln -sf ../../cli cli && ln -sf ../../templates templates && ln -sf ../../evaluation evaluation
```
Also add `/workspace` to the `.pth` file (`_solace_agent_mesh.pth`) so that top-level packages like `config_portal` and `cli` are importable:
```
printf '/workspace/src\n/workspace\n' > .venv/lib/python3.12/site-packages/_solace_agent_mesh.pth
```

### MedExpert application

- **Location**: `medexpert/`
- Requires Redis (`sudo apt-get install -y redis-server && redis-server --daemonize yes`), an `OPENROUTER_API_KEY`, and `.env` file (copy from `.env.example`)
- Set `MEDEXPERT_ENV=development` in `.env` to bypass session secret validation in dev
- Install: `uv pip install -e medexpert/ --no-deps` (the `solace-agent-mesh>=0.9,<1.0` constraint conflicts with the local 1.x editable install; use `--no-deps` and install extras separately: `uv pip install "biopython>=1.83,<2.0" "redis[hiredis]>=5.0,<6.0"`)
- Tests: `pytest tests/unit -v`, `pytest tests/contract -v`

### Running the full MedExpert stack

**Critical**: In dev mode (`SOLACE_DEV_MODE=true`), the DevBroker is in-process only. All agents and the gateway **must** run in a single `sam run` process. Do NOT start them as separate processes (as `start_agents.sh` does) or they cannot communicate.

Start the full stack:
```bash
cd medexpert && set -a && source .env && set +a && sam run configs/
```

This starts all 11 agents + the gateway in one process. MCP servers are separate Python processes (started by `scripts/start_mcp_servers.sh`) and don't use the broker.

To send queries via CLI (bypasses frontend SSE issues): `sam task send "Your question" --agent OrchestratorAgent`

### LLM configuration (OpenRouter + Gemini 3.7 Flash)

All agents use `openrouter/google/gemini-3.7-flash` via OpenRouter's OpenAI-compatible API. Set `OPENROUTER_API_KEY` in your `.env` file. No Vertex AI or Google AI Studio credentials are needed.

### Known pre-existing issues

- **Gateway visualization KeyError**: `KeyError: 'medexpert_topics'` in `component.py:1163` causes 500 errors on SSE visualization subscriptions, blocking the frontend chat UI from displaying messages. The CLI (`sam task send`) works as a workaround.
- **Ruff lint**: ~11k pre-existing lint errors (mostly import ordering)
- **TypeScript types**: `npm run build-types` has pre-existing TS errors in the WebUI
