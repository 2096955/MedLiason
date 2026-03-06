"""Tests for LLM model failover chains (Phase 1).

Validates that LiteLlm retries on transient errors and falls back to
alternative models when the primary model is unavailable.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parents[3]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_litellm(**overrides):
    """Create a LiteLlm instance with mocked LLM client."""
    from solace_agent_mesh.agent.adk.models.lite_llm import LiteLlm

    defaults = {
        "model": "vertex_ai/gemini-2.5-flash",
        "cache_strategy": "none",
    }
    defaults.update(overrides)
    instance = LiteLlm(**defaults)
    instance.llm_client = AsyncMock()
    return instance


class FakeTransientError(Exception):
    """Simulates a transient LLM error with a status_code attribute."""

    def __init__(self, message="rate limit exceeded", status_code=429):
        super().__init__(message)
        self.status_code = status_code


class FakeAuthError(Exception):
    """Simulates a non-transient auth error."""

    def __init__(self, message="invalid api key", status_code=401):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Tests: Initialization
# ---------------------------------------------------------------------------

class TestFailoverInit:
    """Tests for failover configuration parsing."""

    def test_fallbacks_extracted_from_kwargs(self):
        llm = _make_litellm(fallbacks=["vertex_ai/gemini-2.5-pro"])
        assert llm._fallback_models == ["vertex_ai/gemini-2.5-pro"]

    def test_fallbacks_default_empty(self):
        llm = _make_litellm()
        assert llm._fallback_models == []

    def test_num_retries_extracted(self):
        llm = _make_litellm(num_retries=5)
        assert llm._num_retries == 5

    def test_num_retries_default_matches_setup(self):
        """Default num_retries should be 3 (aligned with setup.py setdefault)."""
        llm = _make_litellm()
        assert llm._num_retries == 3

    def test_fallbacks_not_passed_to_additional_args(self):
        llm = _make_litellm(
            fallbacks=["vertex_ai/gemini-2.5-pro"],
            num_retries=3,
        )
        assert "fallbacks" not in llm._additional_args
        assert "num_retries" not in llm._additional_args

    def test_fallbacks_none_becomes_empty_list(self):
        llm = _make_litellm(fallbacks=None)
        assert llm._fallback_models == []


# ---------------------------------------------------------------------------
# Tests: Transient error classification (static method)
# ---------------------------------------------------------------------------

class TestIsTransientError:
    """Tests for the _is_transient_error static method."""

    @pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504, 529])
    def test_transient_status_codes(self, status_code):
        from solace_agent_mesh.agent.adk.models.lite_llm import LiteLlm
        e = FakeTransientError(f"error {status_code}", status_code)
        assert LiteLlm._is_transient_error(e) is True

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
    def test_non_transient_status_codes(self, status_code):
        from solace_agent_mesh.agent.adk.models.lite_llm import LiteLlm
        e = FakeTransientError(f"error {status_code}", status_code)
        assert LiteLlm._is_transient_error(e) is False

    @pytest.mark.parametrize(
        "message",
        [
            "rate limit exceeded",
            "model is overloaded",
            "request timeout after 30s",
            "Quota exceeded for this project",
            "service unavailable",
            "bad gateway",
            "internal server error",
            "connection reset by peer",
            "connection error: ECONNREFUSED",
        ],
    )
    def test_transient_by_message_pattern(self, message):
        from solace_agent_mesh.agent.adk.models.lite_llm import LiteLlm
        e = Exception(message)
        assert LiteLlm._is_transient_error(e) is True

    def test_non_transient_message(self):
        from solace_agent_mesh.agent.adk.models.lite_llm import LiteLlm
        e = Exception("Invalid model name: foo/bar")
        assert LiteLlm._is_transient_error(e) is False


# ---------------------------------------------------------------------------
# Tests: Retry behavior
# ---------------------------------------------------------------------------

class TestRetryBehavior:
    """Tests for transient error retry logic."""

    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        llm = _make_litellm(num_retries=2)
        llm.llm_client.acompletion.return_value = MagicMock(spec=["choices"])

        result = await llm._acompletion_with_fallback(
            model="vertex_ai/gemini-2.5-flash",
            messages=[],
            tools=None,
        )

        assert result is not None
        assert llm.llm_client.acompletion.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_429(self):
        llm = _make_litellm(num_retries=2)
        llm.llm_client.acompletion.side_effect = [
            FakeTransientError("rate limit", 429),
            MagicMock(spec=["choices"]),
        ]

        result = await llm._acompletion_with_fallback(
            model="vertex_ai/gemini-2.5-flash",
            messages=[],
            tools=None,
        )

        assert result is not None
        assert llm.llm_client.acompletion.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_503(self):
        llm = _make_litellm(num_retries=2)
        llm.llm_client.acompletion.side_effect = [
            FakeTransientError("service unavailable", 503),
            FakeTransientError("service unavailable", 503),
            MagicMock(spec=["choices"]),
        ]

        result = await llm._acompletion_with_fallback(
            model="vertex_ai/gemini-2.5-flash",
            messages=[],
            tools=None,
        )

        assert result is not None
        assert llm.llm_client.acompletion.call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_auth_error(self):
        llm = _make_litellm(num_retries=2)
        llm.llm_client.acompletion.side_effect = FakeAuthError()

        with pytest.raises(FakeAuthError):
            await llm._acompletion_with_fallback(
                model="vertex_ai/gemini-2.5-flash",
                messages=[],
                tools=None,
            )

        assert llm.llm_client.acompletion.call_count == 1

    @pytest.mark.asyncio
    async def test_backoff_delay_between_retries(self):
        """Verify asyncio.sleep is called between retry attempts."""
        llm = _make_litellm(num_retries=1)
        llm.llm_client.acompletion.side_effect = [
            FakeTransientError("rate limit", 429),
            MagicMock(spec=["choices"]),
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await llm._acompletion_with_fallback(
                model="vertex_ai/gemini-2.5-flash",
                messages=[],
                tools=None,
            )
            # Sleep called once (between attempt 1 and attempt 2)
            assert mock_sleep.call_count == 1
            delay = mock_sleep.call_args[0][0]
            # Backoff for attempt 0: 2^0 + jitter = 1.0-2.0
            assert 1.0 <= delay <= 2.0

    @pytest.mark.asyncio
    async def test_no_sleep_after_last_retry(self):
        """No sleep call after the final failed attempt on a model."""
        llm = _make_litellm(
            num_retries=1,
            fallbacks=["vertex_ai/gemini-2.5-pro"],
        )
        llm.llm_client.acompletion.side_effect = [
            FakeTransientError("rate limit", 429),
            FakeTransientError("rate limit", 429),
            MagicMock(spec=["choices"]),
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await llm._acompletion_with_fallback(
                model="vertex_ai/gemini-2.5-flash",
                messages=[],
                tools=None,
            )
            # Sleep called once (between attempt 1 and 2 on primary).
            # No sleep after last retry on primary (transitions to fallback).
            assert mock_sleep.call_count == 1


# ---------------------------------------------------------------------------
# Tests: Fallback behavior
# ---------------------------------------------------------------------------

class TestFallbackBehavior:
    """Tests for model failover to fallback models."""

    @pytest.mark.asyncio
    async def test_falls_back_to_secondary_model(self):
        llm = _make_litellm(
            num_retries=0,
            fallbacks=["vertex_ai/gemini-2.5-pro"],
        )
        llm.llm_client.acompletion.side_effect = [
            FakeTransientError("rate limit", 429),
            MagicMock(spec=["choices"]),
        ]

        result = await llm._acompletion_with_fallback(
            model="vertex_ai/gemini-2.5-flash",
            messages=[{"role": "user", "content": "test"}],
            tools=None,
        )

        assert result is not None
        calls = llm.llm_client.acompletion.call_args_list
        assert calls[0].kwargs["model"] == "vertex_ai/gemini-2.5-flash"
        assert calls[1].kwargs["model"] == "vertex_ai/gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_retries_then_falls_back(self):
        llm = _make_litellm(
            num_retries=1,
            fallbacks=["vertex_ai/gemini-2.5-pro"],
        )
        llm.llm_client.acompletion.side_effect = [
            FakeTransientError("rate limit", 429),
            FakeTransientError("rate limit", 429),
            MagicMock(spec=["choices"]),
        ]

        result = await llm._acompletion_with_fallback(
            model="vertex_ai/gemini-2.5-flash",
            messages=[],
            tools=None,
        )

        assert result is not None
        # 2 attempts on primary (1 + 1 retry) + 1 on fallback
        assert llm.llm_client.acompletion.call_count == 3

    @pytest.mark.asyncio
    async def test_all_models_fail_raises_last_error(self):
        llm = _make_litellm(
            num_retries=0,
            fallbacks=["vertex_ai/gemini-2.5-pro"],
        )
        llm.llm_client.acompletion.side_effect = FakeTransientError("overloaded", 529)

        with pytest.raises(FakeTransientError, match="overloaded"):
            await llm._acompletion_with_fallback(
                model="vertex_ai/gemini-2.5-flash",
                messages=[],
                tools=None,
            )

        # 1 attempt on primary + 1 attempt on fallback
        assert llm.llm_client.acompletion.call_count == 2

    @pytest.mark.asyncio
    async def test_no_fallbacks_exhausts_retries(self):
        llm = _make_litellm(num_retries=2)
        llm.llm_client.acompletion.side_effect = FakeTransientError("rate limit", 429)

        with pytest.raises(FakeTransientError):
            await llm._acompletion_with_fallback(
                model="vertex_ai/gemini-2.5-flash",
                messages=[],
                tools=None,
            )

        # 3 attempts total (1 + 2 retries)
        assert llm.llm_client.acompletion.call_count == 3

    @pytest.mark.asyncio
    async def test_multiple_fallbacks(self):
        llm = _make_litellm(
            num_retries=0,
            fallbacks=["vertex_ai/gemini-2.5-pro", "vertex_ai/gemini-2.5-flash-lite"],
        )
        llm.llm_client.acompletion.side_effect = [
            FakeTransientError("rate limit", 429),
            FakeTransientError("rate limit", 429),
            MagicMock(spec=["choices"]),
        ]

        result = await llm._acompletion_with_fallback(
            model="vertex_ai/gemini-2.5-flash",
            messages=[],
            tools=None,
        )

        assert result is not None
        calls = llm.llm_client.acompletion.call_args_list
        assert calls[0].kwargs["model"] == "vertex_ai/gemini-2.5-flash"
        assert calls[1].kwargs["model"] == "vertex_ai/gemini-2.5-pro"
        assert calls[2].kwargs["model"] == "vertex_ai/gemini-2.5-flash-lite"


# ---------------------------------------------------------------------------
# Tests: Config parity
# ---------------------------------------------------------------------------

class TestConfigParity:
    """Verify all 3 shared configs have failover configured."""

    @pytest.mark.parametrize(
        "config_path",
        [
            "medexpert/configs/shared_config.yaml",
            "medexpert/configs/pro/shared_config_pro.yaml",
            "medexpert/configs/opus/shared_config_opus.yaml",
        ],
    )
    def test_all_model_tiers_have_retries(self, config_path):
        import yaml

        full_path = REPO_ROOT / config_path
        with open(full_path) as f:
            raw = yaml.safe_load(f)

        models_item = None
        for item in raw["shared_config"]:
            if isinstance(item, dict) and "models" in item:
                models_item = item
                break

        assert models_item is not None, f"No models section in {config_path}"

        for tier in ["orchestrator", "specialist", "advisory", "verifier", "reviser"]:
            assert tier in models_item, f"{tier} not found in {config_path}"
            tier_config = models_item[tier]
            assert "num_retries" in tier_config, f"{tier} missing num_retries in {config_path}"
            assert tier_config["num_retries"] >= 1, f"{tier} num_retries too low in {config_path}"

    @pytest.mark.parametrize(
        "config_path",
        [
            "medexpert/configs/shared_config.yaml",
            "medexpert/configs/pro/shared_config_pro.yaml",
            "medexpert/configs/opus/shared_config_opus.yaml",
        ],
    )
    def test_non_verifier_tiers_have_fallbacks(self, config_path):
        """Non-verifier tiers should have fallback models configured."""
        import yaml

        full_path = REPO_ROOT / config_path
        with open(full_path) as f:
            raw = yaml.safe_load(f)

        models_item = None
        for item in raw["shared_config"]:
            if isinstance(item, dict) and "models" in item:
                models_item = item
                break

        for tier in ["orchestrator", "specialist", "advisory", "reviser"]:
            tier_config = models_item[tier]
            assert "fallbacks" in tier_config, f"{tier} missing fallbacks in {config_path}"
            assert len(tier_config["fallbacks"]) >= 1, f"{tier} has empty fallbacks in {config_path}"

    @pytest.mark.parametrize(
        "config_path",
        [
            "medexpert/configs/shared_config.yaml",
            "medexpert/configs/pro/shared_config_pro.yaml",
            "medexpert/configs/opus/shared_config_opus.yaml",
        ],
    )
    def test_verifier_has_no_fallback(self, config_path):
        """Verifier must NOT have fallbacks — verification must use the stronger model."""
        import yaml

        full_path = REPO_ROOT / config_path
        with open(full_path) as f:
            raw = yaml.safe_load(f)

        models_item = None
        for item in raw["shared_config"]:
            if isinstance(item, dict) and "models" in item:
                models_item = item
                break

        verifier = models_item["verifier"]
        assert "fallbacks" not in verifier, (
            f"Verifier in {config_path} should NOT have fallbacks "
            "(verification must use the stronger model for factual accuracy)"
        )
