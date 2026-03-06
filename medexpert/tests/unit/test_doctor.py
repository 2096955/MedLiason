"""Unit tests for medexpert/scripts/doctor.py health check."""

import json
import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import doctor


# ---------------------------------------------------------------------------
# Redis checks
# ---------------------------------------------------------------------------


class TestCheckRedis:
    def test_redis_pass(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.scan.return_value = (0, [b"medexpert:a", b"medexpert:b"])

        with patch.dict(os.environ, {"REDIS_URL": "redis://myhost:6380/0"}):
            with patch("redis.Redis.from_url", return_value=mock_client):
                result = doctor.check_redis()

        assert result.status == "PASS"
        assert "myhost:6380" in result.detail
        assert "2 keys" in result.detail

    def test_redis_fail_connection(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}):
            with patch("redis.Redis.from_url", side_effect=ConnectionError("refused")):
                result = doctor.check_redis()

        assert result.status == "FAIL"
        assert "ConnectionError" in result.detail

    def test_redis_fail_import(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/0"}):
            with patch.dict(sys.modules, {"redis": None}):
                # Force ImportError by removing the module
                import importlib

                # Simulating import failure
                with patch("builtins.__import__", side_effect=_import_raiser("redis")):
                    result = doctor.check_redis()

        assert result.status == "FAIL"

    def test_redis_default_url(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.scan.return_value = (0, [])

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REDIS_URL", None)
            with patch("redis.Redis.from_url", return_value=mock_client) as mock_from:
                result = doctor.check_redis()

        mock_from.assert_called_once()
        assert "redis://localhost:6379/0" in mock_from.call_args[0]
        assert result.status == "PASS"
        assert "0 keys" in result.detail


# ---------------------------------------------------------------------------
# MCP server checks
# ---------------------------------------------------------------------------


class TestCheckMcpServer:
    def test_mcp_pass(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_cls.return_value.__enter__.return_value.get.return_value = mock_response
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            result = doctor.check_mcp_server(9001, "pubmed")

        assert result.status == "PASS"
        assert "responding" in result.detail

    def test_mcp_fail_connection_tier1(self):
        import httpx

        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.get.side_effect = httpx.ConnectError("refused")
            mock_cls.return_value.__enter__ = MagicMock(return_value=ctx)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            result = doctor.check_mcp_server(9001, "pubmed")

        assert result.status == "FAIL"
        assert "connection refused" in result.detail

    def test_mcp_skip_tier2(self):
        import httpx

        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.get.side_effect = httpx.ConnectError("refused")
            mock_cls.return_value.__enter__ = MagicMock(return_value=ctx)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            result = doctor.check_mcp_server(9011, "knowledge_graph")

        assert result.status == "SKIP"
        assert "Tier-1" in result.detail

    def test_mcp_pass_read_timeout(self):
        """SSE endpoints may hold connections open — ReadTimeout means alive."""
        import httpx

        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.get.side_effect = httpx.ReadTimeout("held open")
            mock_cls.return_value.__enter__ = MagicMock(return_value=ctx)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            result = doctor.check_mcp_server(9001, "pubmed")

        assert result.status == "PASS"
        assert "SSE hold" in result.detail


# ---------------------------------------------------------------------------
# Cold store checks
# ---------------------------------------------------------------------------


class TestCheckColdStore:
    def test_cold_store_pass(self, tmp_path):
        db_path = str(tmp_path / "test-cold.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 7")
        conn.execute(
            "CREATE TABLE session_outcomes (id INTEGER PRIMARY KEY, data TEXT)"
        )
        conn.execute("INSERT INTO session_outcomes VALUES (1, 'a')")
        conn.execute("INSERT INTO session_outcomes VALUES (2, 'b')")
        conn.commit()
        conn.close()

        with patch.dict(os.environ, {"COLD_DB_PATH": db_path}):
            result = doctor.check_cold_store()

        assert result.status == "PASS"
        assert "v7" in result.detail
        assert "2 sessions" in result.detail

    def test_cold_store_missing(self, tmp_path):
        db_path = str(tmp_path / "nonexistent.db")
        with patch.dict(os.environ, {"COLD_DB_PATH": db_path}):
            result = doctor.check_cold_store()

        assert result.status == "WARN"
        assert "not found" in result.detail

    def test_cold_store_no_table(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        conn.close()

        with patch.dict(os.environ, {"COLD_DB_PATH": db_path}):
            result = doctor.check_cold_store()

        assert result.status == "PASS"
        assert "v5" in result.detail
        assert "table missing" in result.detail


# ---------------------------------------------------------------------------
# Environment variable checks
# ---------------------------------------------------------------------------


class TestCheckEnvVars:
    def test_gemini_key_set(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test123"}, clear=False):
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            results = doctor.check_env_vars()

        auth_result = results[0]
        assert auth_result.status == "PASS"
        assert "GEMINI_API_KEY" in auth_result.name

    def test_gac_set(self):
        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/path/key.json"}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            results = doctor.check_env_vars()

        auth_result = results[0]
        assert auth_result.status == "PASS"
        assert "GOOGLE_APPLICATION_CREDENTIALS" in auth_result.name

    def test_neither_auth_set(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            results = doctor.check_env_vars()

        auth_result = results[0]
        assert auth_result.status == "FAIL"

    def test_ncbi_warn(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x"}, clear=False):
            os.environ.pop("NCBI_API_KEY", None)
            results = doctor.check_env_vars()

        ncbi = [r for r in results if "NCBI" in r.name][0]
        assert ncbi.status == "WARN"
        assert "3 req/s" in ncbi.detail


# ---------------------------------------------------------------------------
# Memgraph checks
# ---------------------------------------------------------------------------


class TestCheckMemgraph:
    def test_memgraph_skip_no_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMGRAPH_URL", None)
            result = doctor.check_memgraph()

        assert result.status == "SKIP"

    def test_memgraph_pass(self):
        mock_driver = MagicMock()
        mock_graph_db = MagicMock()
        mock_graph_db.driver.return_value = mock_driver
        mock_neo4j = MagicMock()
        mock_neo4j.GraphDatabase = mock_graph_db

        with patch.dict(os.environ, {"MEMGRAPH_URL": "bolt://localhost:7687"}):
            with patch.dict(sys.modules, {"neo4j": mock_neo4j}):
                result = doctor.check_memgraph()

        assert result.status == "PASS"
        mock_driver.verify_connectivity.assert_called_once()
        mock_driver.close.assert_called_once()

    def test_memgraph_fail(self):
        mock_graph_db = MagicMock()
        mock_graph_db.driver.side_effect = Exception("connection refused")
        mock_neo4j = MagicMock()
        mock_neo4j.GraphDatabase = mock_graph_db

        with patch.dict(os.environ, {"MEMGRAPH_URL": "bolt://localhost:7687"}):
            with patch.dict(sys.modules, {"neo4j": mock_neo4j}):
                result = doctor.check_memgraph()

        assert result.status == "FAIL"


# ---------------------------------------------------------------------------
# Integration: main() and output
# ---------------------------------------------------------------------------


class TestMain:
    def test_exit_code_zero_on_all_pass(self):
        pass_result = doctor.CheckResult("test", "PASS", "ok")
        with patch.object(doctor, "run_all_checks", return_value=[pass_result]):
            with patch("sys.argv", ["doctor.py"]):
                code = doctor.main()
        assert code == 0

    def test_exit_code_one_on_fail(self):
        results = [
            doctor.CheckResult("good", "PASS", "ok"),
            doctor.CheckResult("bad", "FAIL", "broken"),
        ]
        with patch.object(doctor, "run_all_checks", return_value=results):
            with patch("sys.argv", ["doctor.py"]):
                code = doctor.main()
        assert code == 1

    def test_json_output(self, capsys):
        results = [
            doctor.CheckResult("Redis", "PASS", "ok"),
            doctor.CheckResult("MCP foo", "FAIL", "down"),
            doctor.CheckResult("Env X", "WARN", "missing"),
        ]
        with patch.object(doctor, "run_all_checks", return_value=results):
            with patch("sys.argv", ["doctor.py", "--json"]):
                doctor.main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["summary"]["pass"] == 1
        assert data["summary"]["fail"] == 1
        assert data["summary"]["warn"] == 1
        assert len(data["checks"]) == 3

    def test_warn_and_skip_are_exit_zero(self):
        results = [
            doctor.CheckResult("a", "WARN", "w"),
            doctor.CheckResult("b", "SKIP", "s"),
        ]
        with patch.object(doctor, "run_all_checks", return_value=results):
            with patch("sys.argv", ["doctor.py"]):
                code = doctor.main()
        assert code == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_raiser(blocked_module):
    """Return an __import__ replacement that raises ImportError for a module."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _fake_import(name, *args, **kwargs):
        if name == blocked_module:
            raise ImportError(f"No module named '{blocked_module}'")
        return real_import(name, *args, **kwargs)

    return _fake_import
