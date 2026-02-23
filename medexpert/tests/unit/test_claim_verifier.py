"""Tests for ClaimVerifierTool — claim-citation alignment analysis."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lifesci_tools.claim_verifier import (
    ClaimVerifierTool,
    _extract_claims_with_citations,
    _extract_keywords,
)


@pytest.fixture
def tool():
    """Keyword-only tool (no LLM calls) — preserves all existing test behaviour."""
    return ClaimVerifierTool(tool_config={"use_llm": False})


@pytest.fixture
def ctx():
    return MagicMock()


# ── _extract_keywords ─────────────────────────────────────────


def test_extract_keywords_removes_stop_words():
    keywords = _extract_keywords("The drug is very effective for the treatment.")
    assert "the" not in keywords
    assert "very" not in keywords
    assert "drug" in keywords
    assert "effective" in keywords
    assert "treatment" in keywords


def test_extract_keywords_minimum_length():
    keywords = _extract_keywords("A is by of the RNA DNA an")
    # Only words with 4+ chars
    assert not any(len(k) < 4 for k in keywords)


# ── _extract_claims_with_citations ────────────────────────────


def test_extract_single_citation():
    text = "Metformin reduces HbA1c by 1.5%. [[cite:s0r0]]"
    claims = _extract_claims_with_citations(text)
    assert len(claims) == 1
    assert claims[0]["cited_ids"] == ["s0r0"]
    assert "Metformin" in claims[0]["claim_text"]
    assert "[[cite:" not in claims[0]["claim_text"]


def test_extract_multiple_citations():
    text = "Drug A is effective. [[cite:s0r0]] Drug B is safe. [[cite:s0r1]]"
    claims = _extract_claims_with_citations(text)
    assert len(claims) == 2


def test_extract_no_citations():
    text = "This is a statement without any citations."
    claims = _extract_claims_with_citations(text)
    assert len(claims) == 0


def test_extract_multiple_cites_in_one_sentence():
    text = "Both drugs are effective. [[cite:s0r0]] [[cite:s0r1]]"
    claims = _extract_claims_with_citations(text)
    assert len(claims) == 1
    assert len(claims[0]["cited_ids"]) == 2


# ── well-supported claims ────────────────────────────────────


async def test_well_supported_claim(tool, ctx):
    response_text = "Metformin effectively reduces blood glucose levels in diabetic patients. [[cite:s0r0]]"
    citations = [
        {
            "id": "s0r0",
            "title": "Metformin for Diabetes",
            "snippet": "Metformin reduces blood glucose effectively in patients with diabetes.",
            "url": "https://example.com",
        }
    ]
    result = await tool._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert result["total_claims"] == 1
    assert result["claims"][0]["supported"] is True
    assert result["claims"][0]["alignment_score"] > 0.15
    assert result["overall_score"] > 0


# ── misattributed citations ──────────────────────────────────


async def test_misattributed_citation(tool, ctx):
    response_text = "Aspirin prevents cardiovascular events. [[cite:s0r0]]"
    citations = [
        {
            "id": "s0r0",
            "title": "Nutrition and Diet",
            "snippet": "Mediterranean diet improves cholesterol levels.",
            "url": "https://example.com",
        }
    ]
    result = await tool._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert result["claims"][0]["supported"] is False
    assert len(result["unsupported_claims"]) >= 1


# ── uncited claims (citation ID not found) ────────────────────


async def test_citation_id_not_found(tool, ctx):
    response_text = "This drug is effective. [[cite:s9r9]]"
    citations = [
        {"id": "s0r0", "title": "Other Study", "snippet": "Unrelated content.", "url": "https://example.com"}
    ]
    result = await tool._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert result["claims"][0]["supported"] is False
    assert result["claims"][0]["reason"] == "Citation ID not found"


# ── multiple claims ───────────────────────────────────────────


async def test_multiple_claims_mixed_support(tool, ctx):
    response_text = (
        "Metformin is the first-line treatment for diabetes. [[cite:s0r0]] "
        "Yoga cures all diseases. [[cite:s0r1]]"
    )
    citations = [
        {
            "id": "s0r0",
            "title": "Diabetes Treatment Guidelines",
            "snippet": "Metformin is recommended as first-line treatment for type 2 diabetes.",
            "url": "https://example.com",
        },
        {
            "id": "s0r1",
            "title": "Exercise and Health",
            "snippet": "Regular physical exercise improves cardiovascular health.",
            "url": "https://example.com",
        },
    ]
    result = await tool._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert result["total_claims"] == 2
    assert result["supported_count"] >= 1
    assert len(result["unsupported_claims"]) >= 1


# ── alignment scoring ────────────────────────────────────────


async def test_alignment_score_between_0_and_1(tool, ctx):
    response_text = "Statins reduce cholesterol. [[cite:s0r0]]"
    citations = [
        {
            "id": "s0r0",
            "title": "Statin Therapy",
            "snippet": "Statins effectively lower LDL cholesterol levels.",
            "url": "https://example.com",
        }
    ]
    result = await tool._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    score = result["claims"][0]["alignment_score"]
    assert 0.0 <= score <= 1.0


async def test_keywords_matched_returned(tool, ctx):
    response_text = "Metformin reduces glucose levels. [[cite:s0r0]]"
    citations = [
        {
            "id": "s0r0",
            "title": "Metformin Study",
            "snippet": "Metformin glucose reduction observed.",
            "url": "https://example.com",
        }
    ]
    result = await tool._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert len(result["claims"][0]["keywords_matched"]) > 0


# ── error handling ────────────────────────────────────────────


async def test_empty_response_text(tool, ctx):
    result = await tool._run_async_impl(
        {"response_text": "", "citations_json": "[]"}, ctx
    )
    assert "error" in result


async def test_invalid_citations_json(tool, ctx):
    result = await tool._run_async_impl(
        {"response_text": "Test.", "citations_json": "bad"}, ctx
    )
    assert "error" in result


async def test_no_citations_in_text(tool, ctx):
    result = await tool._run_async_impl(
        {
            "response_text": "This is text without any citations.",
            "citations_json": json.dumps([{"id": "s0r0", "title": "T", "snippet": "S", "url": "u"}]),
        },
        ctx,
    )
    assert result["total_claims"] == 0
    assert result["overall_score"] == 0.0


# ── overall score ─────────────────────────────────────────────


async def test_overall_score_average(tool, ctx):
    response_text = (
        "Claim A about treatment. [[cite:s0r0]] "
        "Claim B about treatment. [[cite:s0r0]]"
    )
    citations = [
        {
            "id": "s0r0",
            "title": "Treatment Study",
            "snippet": "Treatment is effective for disease management.",
            "url": "https://example.com",
        }
    ]
    result = await tool._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert 0.0 <= result["overall_score"] <= 1.0


# ── new output fields (keyword mode) ─────────────────────────


async def test_keyword_mode_output_fields(tool, ctx):
    """Keyword-mode result includes method, entailment_summary, and token counts."""
    response_text = "Statins lower cholesterol. [[cite:s0r0]]"
    citations = [
        {"id": "s0r0", "title": "Statin Study", "snippet": "Statins lower LDL cholesterol.", "url": "u"}
    ]
    result = await tool._run_async_impl(
        {"response_text": response_text, "citations_json": json.dumps(citations)}, ctx
    )
    assert result["method"] == "keyword"
    assert result["entailment_summary"] == {"ENTAILS": 0, "CONTRADICTS": 0, "NEUTRAL": 0}
    assert result["total_prompt_tokens"] == 0
    assert result["total_completion_tokens"] == 0
    assert result["claims"][0]["method"] == "keyword"


# ═══════════════════════════════════════════════════════════════
# LLM Entailment tests (mocked)
# ═══════════════════════════════════════════════════════════════


class TestLLMEntailment:
    """Tests with mocked litellm.acompletion calls."""

    @pytest.fixture
    def llm_tool(self):
        return ClaimVerifierTool(tool_config={"use_llm": True, "model": "openai/gemini-2.5-flash-001"})

    @pytest.fixture
    def llm_ctx(self):
        c = MagicMock()
        c.session = MagicMock()
        c.session.id = "test-sess"
        return c

    def _mock_response(self, entailment, confidence, excerpt="relevant text"):
        """Create a mock litellm response."""
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps({
            "entailment": entailment,
            "confidence": confidence,
            "evidence_excerpt": excerpt,
        })
        resp.usage = MagicMock()
        resp.usage.prompt_tokens = 50
        resp.usage.completion_tokens = 20
        return resp

    async def test_entails_claim(self, llm_tool, llm_ctx):
        mock_resp = self._mock_response("ENTAILS", 0.95, "metformin reduces HbA1c")
        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, return_value=mock_resp):
            result = await llm_tool._run_async_impl(
                {
                    "response_text": "Metformin reduces HbA1c. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Metformin Study", "snippet": "Metformin reduces HbA1c.", "url": "u"}
                    ]),
                },
                llm_ctx,
            )
        assert result["claims"][0]["entailment"] == "ENTAILS"
        assert result["claims"][0]["confidence"] == 0.95
        assert result["claims"][0]["supported"] is True
        assert result["claims"][0]["method"] == "llm"
        assert result["method"] == "llm"

    async def test_contradicts_claim(self, llm_tool, llm_ctx):
        mock_resp = self._mock_response("CONTRADICTS", 0.9, "aspirin not for this")
        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, return_value=mock_resp):
            result = await llm_tool._run_async_impl(
                {
                    "response_text": "Aspirin cures cancer. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Aspirin Study", "snippet": "Aspirin for headaches.", "url": "u"}
                    ]),
                },
                llm_ctx,
            )
        assert result["claims"][0]["entailment"] == "CONTRADICTS"
        assert result["claims"][0]["supported"] is False
        assert result["claims"][0]["alignment_score"] == 0.0

    async def test_fallback_on_llm_failure(self, llm_tool, llm_ctx):
        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, side_effect=RuntimeError("API down")):
            result = await llm_tool._run_async_impl(
                {
                    "response_text": "Statins lower cholesterol. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Statin Therapy", "snippet": "Statins lower LDL cholesterol.", "url": "u"}
                    ]),
                },
                llm_ctx,
            )
        # Should fall back to keyword
        assert result["claims"][0]["method"] == "keyword"
        assert result["method"] == "mixed"

    async def test_entailment_summary_counts(self, llm_tool, llm_ctx):
        responses = [
            self._mock_response("ENTAILS", 0.9),
            self._mock_response("CONTRADICTS", 0.8),
        ]
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, side_effect=side_effect):
            result = await llm_tool._run_async_impl(
                {
                    "response_text": "Claim one. [[cite:s0r0]] Claim two. [[cite:s0r1]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "S1", "snippet": "s1", "url": "u"},
                        {"id": "s0r1", "title": "S2", "snippet": "s2", "url": "u"},
                    ]),
                },
                llm_ctx,
            )
        assert result["entailment_summary"]["ENTAILS"] == 1
        assert result["entailment_summary"]["CONTRADICTS"] == 1
        assert result["entailment_summary"]["NEUTRAL"] == 0

    async def test_prompt_construction(self, llm_tool, llm_ctx):
        """Verify the prompt sent to the LLM contains claim and source."""
        captured_kwargs = {}
        mock_resp = self._mock_response("ENTAILS", 0.9)

        async def capture(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_resp

        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, side_effect=capture):
            await llm_tool._run_async_impl(
                {
                    "response_text": "Metformin works. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Met Study", "snippet": "Metformin effective.", "url": "u"}
                    ]),
                },
                llm_ctx,
            )
        prompt_content = captured_kwargs["messages"][0]["content"]
        assert "Metformin works" in prompt_content
        assert "Met Study" in prompt_content or "Metformin effective" in prompt_content

    async def test_token_aggregation(self, llm_tool, llm_ctx):
        """total_prompt_tokens and total_completion_tokens aggregate across claims."""
        responses = [
            self._mock_response("ENTAILS", 0.9),
            self._mock_response("NEUTRAL", 0.5),
        ]
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, side_effect=side_effect):
            result = await llm_tool._run_async_impl(
                {
                    "response_text": "Claim one. [[cite:s0r0]] Claim two. [[cite:s0r1]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "S1", "snippet": "s1", "url": "u"},
                        {"id": "s0r1", "title": "S2", "snippet": "s2", "url": "u"},
                    ]),
                },
                llm_ctx,
            )
        # Each mock returns 50 prompt + 20 completion tokens
        assert result["total_prompt_tokens"] == 100
        assert result["total_completion_tokens"] == 40

    async def test_unexpected_entailment_label_normalised(self, llm_tool, llm_ctx):
        """Unknown entailment labels from LLM are normalised to NEUTRAL."""
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps({
            "entailment": "PARTIALLY_SUPPORTED",
            "confidence": 0.6,
            "evidence_excerpt": "some text",
        })
        resp.usage = MagicMock()
        resp.usage.prompt_tokens = 10
        resp.usage.completion_tokens = 5
        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, return_value=resp):
            result = await llm_tool._run_async_impl(
                {
                    "response_text": "Some claim. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Source", "snippet": "text", "url": "u"}
                    ]),
                },
                llm_ctx,
            )
        assert result["claims"][0]["entailment"] == "NEUTRAL"
        assert result["entailment_summary"]["NEUTRAL"] == 1

    async def test_source_with_braces_no_crash(self, llm_tool, llm_ctx):
        """Source text containing { or } chars should not cause format errors."""
        mock_resp = self._mock_response("ENTAILS", 0.9, "relevant")
        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, return_value=mock_resp):
            result = await llm_tool._run_async_impl(
                {
                    "response_text": "JSON data is valid. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Tech Report", "snippet": 'Result: {"count": 5, "valid": true}', "url": "u"}
                    ]),
                },
                llm_ctx,
            )
        # Should not crash — the claim should be processed
        assert result["total_claims"] == 1


# ── model fallback ────────────────────────────────────────────


class TestModelFallback:
    """Tests for the fallback_model feature."""

    @pytest.fixture
    def fallback_tool(self):
        return ClaimVerifierTool(tool_config={
            "use_llm": True,
            "model": "openai/primary-model",
            "fallback_model": "openai/fallback-model",
        })

    @pytest.fixture
    def fb_ctx(self):
        c = MagicMock()
        c.session = MagicMock()
        c.session.id = "fb-test"
        return c

    def _mock_response(self, entailment, confidence):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = json.dumps({
            "entailment": entailment,
            "confidence": confidence,
            "evidence_excerpt": "text",
        })
        resp.usage = MagicMock()
        resp.usage.prompt_tokens = 30
        resp.usage.completion_tokens = 15
        return resp

    async def test_fallback_on_primary_failure(self, fallback_tool, fb_ctx):
        """When primary model fails, fallback model is tried."""
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("model") == "openai/primary-model":
                raise RuntimeError("Primary model unavailable")
            return self._mock_response("ENTAILS", 0.9)

        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, side_effect=side_effect):
            result = await fallback_tool._run_async_impl(
                {
                    "response_text": "Metformin works. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Study", "snippet": "Metformin effective.", "url": "u"}
                    ]),
                },
                fb_ctx,
            )
        assert call_count == 2  # primary + fallback
        assert result["claims"][0]["method"] == "llm_fallback"
        assert result["claims"][0]["entailment"] == "ENTAILS"
        assert result["method"] == "mixed"

    async def test_both_models_fail_falls_to_keyword(self, fallback_tool, fb_ctx):
        """When both primary and fallback fail, keyword is used."""
        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, side_effect=RuntimeError("All models down")):
            result = await fallback_tool._run_async_impl(
                {
                    "response_text": "Statins lower cholesterol. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Statin Study", "snippet": "Statins lower LDL cholesterol.", "url": "u"}
                    ]),
                },
                fb_ctx,
            )
        assert result["claims"][0]["method"] == "keyword"
        assert result["method"] == "mixed"

    async def test_no_fallback_skips_to_keyword(self, fb_ctx):
        """Without fallback_model, primary failure goes straight to keyword."""
        tool_no_fb = ClaimVerifierTool(tool_config={"use_llm": True, "model": "openai/primary-model"})
        with patch("lifesci_tools.claim_verifier._litellm_acompletion", new_callable=AsyncMock, side_effect=RuntimeError("Model down")):
            result = await tool_no_fb._run_async_impl(
                {
                    "response_text": "Drug works. [[cite:s0r0]]",
                    "citations_json": json.dumps([
                        {"id": "s0r0", "title": "Study", "snippet": "Drug effective.", "url": "u"}
                    ]),
                },
                fb_ctx,
            )
        assert result["claims"][0]["method"] == "keyword"


# ── use_llm string coercion ───────────────────────────────────


def test_use_llm_false_string():
    """YAML string 'false' should disable LLM mode."""
    tool = ClaimVerifierTool(tool_config={"use_llm": "false"})
    assert tool.use_llm is False


def test_use_llm_true_string():
    tool = ClaimVerifierTool(tool_config={"use_llm": "true"})
    assert tool.use_llm is True


def test_use_llm_bool_false():
    tool = ClaimVerifierTool(tool_config={"use_llm": False})
    assert tool.use_llm is False


# ═══════════════════════════════════════════════════════════════
# LLM Entailment integration tests (real API calls)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY") or not os.getenv("RUN_LLM_INTEGRATION"),
    reason="GEMINI_API_KEY or RUN_LLM_INTEGRATION not set — skipping live LLM tests",
)
class TestLLMEntailmentIntegration:
    """Integration tests that make real LLM calls (requires GEMINI_API_KEY)."""

    @pytest.fixture
    def live_tool(self):
        return ClaimVerifierTool(tool_config={
            "use_llm": True,
            "model": "openai/gemini-2.5-flash-001",
            "temperature": 0.0,
        })

    @pytest.fixture
    def live_ctx(self):
        c = MagicMock()
        c.session = MagicMock()
        c.session.id = "integration-test"
        return c

    async def test_real_supported_claim(self, live_tool, live_ctx):
        result = await live_tool._run_async_impl(
            {
                "response_text": "Metformin is the first-line treatment for type 2 diabetes. [[cite:s0r0]]",
                "citations_json": json.dumps([{
                    "id": "s0r0",
                    "title": "ADA Diabetes Guidelines 2024",
                    "snippet": "Metformin remains the recommended first-line pharmacologic therapy for type 2 diabetes.",
                    "url": "https://example.com",
                }]),
            },
            live_ctx,
        )
        assert result["claims"][0]["entailment"] == "ENTAILS"
        assert result["claims"][0]["supported"] is True
        assert result["claims"][0]["method"] == "llm"

    async def test_real_contradicted_claim(self, live_tool, live_ctx):
        result = await live_tool._run_async_impl(
            {
                "response_text": "Homeopathy is proven to cure cancer. [[cite:s0r0]]",
                "citations_json": json.dumps([{
                    "id": "s0r0",
                    "title": "Cochrane Review of Homeopathy",
                    "snippet": "There is no reliable evidence that homeopathy is effective for any health condition.",
                    "url": "https://example.com",
                }]),
            },
            live_ctx,
        )
        assert result["claims"][0]["entailment"] in ("CONTRADICTS", "NEUTRAL")
        assert result["claims"][0]["supported"] is False
