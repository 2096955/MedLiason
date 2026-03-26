#!/usr/bin/env python3
"""MedExpert diagnostic health check.

Validates connectivity and health of all MedExpert subsystems:
  - Redis
  - 18 MCP servers
  - Cold store (SQLite)
  - Required environment variables
  - Memgraph (optional)

Usage:
    python scripts/doctor.py          # Colorized terminal output
    python scripts/doctor.py --json   # Machine-readable JSON output

Exit codes:
    0 — all checks PASS, WARN, or SKIP
    1 — one or more checks FAIL
"""

import argparse
import json
import os
import sqlite3
import sys

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_COLORS = {
    "PASS": "\033[32m",  # green
    "FAIL": "\033[31m",  # red
    "WARN": "\033[33m",  # yellow
    "SKIP": "\033[36m",  # cyan
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
}


def _colorize(status: str, text: str) -> str:
    color = _COLORS.get(status, "")
    reset = _COLORS["RESET"]
    return f"{color}[{status}]{reset} {text}"


# ---------------------------------------------------------------------------
# MCP server registry
# ---------------------------------------------------------------------------

MCP_SERVERS = [
    (9001, "pubmed"),
    (9002, "clinicaltrials"),
    (9003, "openfda"),
    (9004, "regulatory"),
    (9005, "cdc"),
    (9006, "census_sdoh"),
    (9007, "environmental"),
    (9008, "seer"),
    (9009, "genomic"),
    (9010, "provider_intel"),
    (9011, "knowledge_graph"),
    (9012, "imaging"),
    (9013, "pharmacovigilance"),
    (9014, "societies"),
    (9015, "vigibase"),
    (9016, "nhs_111"),
    (9017, "bma_library"),
    (9018, "openevidence"),
]

# Tier-2 servers that are commonly skipped in deployment
TIER2_SERVERS = {"knowledge_graph", "societies", "vigibase", "imaging", "pharmacovigilance"}

# ---------------------------------------------------------------------------
# Check results
# ---------------------------------------------------------------------------


class CheckResult:
    """Single health-check result."""

    def __init__(self, name: str, status: str, detail: str):
        self.name = name
        self.status = status  # PASS, FAIL, WARN, SKIP
        self.detail = detail

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}

    def to_line(self) -> str:
        return _colorize(self.status, f"{self.name} — {self.detail}")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_redis() -> CheckResult:
    """Check Redis connectivity and count medexpert keys."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(redis_url, socket_connect_timeout=5)
        client.ping()
        # Count medexpert:* keys using SCAN (safe for production)
        count = 0
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match="medexpert:*", count=500)
            count += len(keys)
            if cursor == 0:
                break
        client.close()
        # Parse host:port for display
        from urllib.parse import urlparse

        parsed = urlparse(redis_url)
        host_port = f"{parsed.hostname or 'localhost'}:{parsed.port or 6379}"
        return CheckResult("Redis", "PASS", f"{host_port} — connected, {count} keys")
    except ImportError:
        return CheckResult("Redis", "FAIL", "redis package not installed")
    except Exception as exc:
        return CheckResult("Redis", "FAIL", f"{type(exc).__name__}: {exc}")


def check_mcp_server(port: int, name: str) -> CheckResult:
    """Check a single MCP server via HTTP GET to /sse."""
    label = f"MCP {name} (localhost:{port})"
    try:
        import httpx

        # SSE endpoint returns a streaming response; we just need to verify
        # the server accepts the connection (2xx or upgrade). Use a short
        # timeout and don't consume the stream.
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"http://localhost:{port}/sse", timeout=5.0)
            if resp.status_code >= 500:
                return CheckResult(label, "WARN", f"HTTP {resp.status_code}")
            return CheckResult(label, "PASS", "responding")
    except ImportError:
        return CheckResult(label, "FAIL", "httpx package not installed")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        if name in TIER2_SERVERS:
            return CheckResult(label, "SKIP", "not in Tier-1")
        return CheckResult(label, "FAIL", "connection refused")
    except httpx.ReadTimeout:
        # SSE endpoint may hold the connection open — that's fine
        return CheckResult(label, "PASS", "responding (SSE hold)")
    except Exception as exc:
        if name in TIER2_SERVERS:
            return CheckResult(label, "SKIP", f"not in Tier-1 ({type(exc).__name__})")
        return CheckResult(label, "FAIL", f"{type(exc).__name__}: {exc}")


def check_cold_store() -> CheckResult:
    """Check cold store SQLite database existence and session count."""
    db_path = os.environ.get("COLD_DB_PATH", "data/medexpert-cold.db")
    label = f"Cold store ({db_path})"
    if not os.path.exists(db_path):
        return CheckResult(label, "WARN", "database file not found")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
        try:
            row = conn.execute("SELECT COUNT(*) FROM session_outcomes").fetchone()
            session_count = row[0]
        except sqlite3.OperationalError:
            session_count = "table missing"
        conn.close()
        return CheckResult(
            label,
            "PASS",
            f"schema v{schema_version}, {session_count} sessions",
        )
    except Exception as exc:
        return CheckResult(label, "FAIL", f"{type(exc).__name__}: {exc}")


def check_env_var(name: str, *, required: bool = True, warn_msg: str = "") -> CheckResult:
    """Check if an environment variable is set."""
    label = f"Env {name}"
    value = os.environ.get(name, "")
    if value:
        return CheckResult(label, "PASS", "set")
    if required:
        return CheckResult(label, "FAIL", "not set")
    return CheckResult(label, "WARN", warn_msg or "not set")


def check_env_vars() -> list[CheckResult]:
    """Check all required/recommended environment variables."""
    results = []
    # At least one of GEMINI_API_KEY or GOOGLE_APPLICATION_CREDENTIALS
    gemini = os.environ.get("GEMINI_API_KEY", "")
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if gemini:
        results.append(CheckResult("Env GEMINI_API_KEY", "PASS", "set"))
    elif gac:
        results.append(CheckResult("Env GOOGLE_APPLICATION_CREDENTIALS", "PASS", "set"))
    else:
        results.append(
            CheckResult(
                "Env GEMINI_API_KEY / GOOGLE_APPLICATION_CREDENTIALS",
                "FAIL",
                "neither set — LLM calls will fail",
            )
        )

    results.append(
        check_env_var(
            "NCBI_API_KEY",
            required=False,
            warn_msg="not set (PubMed limited to 3 req/s)",
        )
    )
    results.append(
        check_env_var(
            "REDIS_URL",
            required=False,
            warn_msg="not set (defaulting to redis://localhost:6379/0)",
        )
    )
    return results


def check_memgraph() -> CheckResult:
    """Check Memgraph connectivity if MEMGRAPH_URL is set."""
    url = os.environ.get("MEMGRAPH_URL", "")
    if not url:
        return CheckResult("Memgraph", "SKIP", "MEMGRAPH_URL not set")
    try:
        from neo4j import GraphDatabase

        user = os.environ.get("MEMGRAPH_USER", "")
        password = os.environ.get("MEMGRAPH_PASSWORD", "")
        auth = (user, password) if user else None
        driver = GraphDatabase.driver(url, auth=auth)
        driver.verify_connectivity()
        driver.close()
        return CheckResult("Memgraph", "PASS", f"connected ({url})")
    except ImportError:
        return CheckResult("Memgraph", "SKIP", "neo4j driver not installed")
    except Exception as exc:
        return CheckResult("Memgraph", "FAIL", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_all_checks() -> list[CheckResult]:
    """Execute all health checks and return results."""
    results = []

    # 1. Redis
    results.append(check_redis())

    # 2. MCP servers
    for port, name in MCP_SERVERS:
        results.append(check_mcp_server(port, name))

    # 3. Cold store
    results.append(check_cold_store())

    # 4. Environment variables
    results.extend(check_env_vars())

    # 5. Memgraph
    results.append(check_memgraph())

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="MedExpert diagnostic health check")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable JSON output",
    )
    args = parser.parse_args()

    results = run_all_checks()

    if args.json_output:
        output = {
            "checks": [r.to_dict() for r in results],
            "summary": {
                "pass": sum(1 for r in results if r.status == "PASS"),
                "fail": sum(1 for r in results if r.status == "FAIL"),
                "warn": sum(1 for r in results if r.status == "WARN"),
                "skip": sum(1 for r in results if r.status == "SKIP"),
            },
        }
        print(json.dumps(output, indent=2))
    else:
        bold = _COLORS["BOLD"]
        reset = _COLORS["RESET"]
        print(f"\n{bold}MedExpert Health Check{reset}")
        print("=" * 40)
        print()
        for result in results:
            print(result.to_line())
        print()
        passes = sum(1 for r in results if r.status == "PASS")
        fails = sum(1 for r in results if r.status == "FAIL")
        warns = sum(1 for r in results if r.status == "WARN")
        skips = sum(1 for r in results if r.status == "SKIP")
        print(
            f"Summary: {passes} passed, {fails} failed, "
            f"{warns} warnings, {skips} skipped"
        )

    has_failures = any(r.status == "FAIL" for r in results)
    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
