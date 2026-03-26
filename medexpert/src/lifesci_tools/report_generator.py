"""Report Generator — multi-format output for research results.

Supports 4 output modes: quick_answer, research_brief, full_synthesis,
and advisory_board_report.  For ``full_synthesis`` and
``advisory_board_report`` modes, when an LLM model is configured
(via ``tool_config``), the tool produces an LLM-written narrative
synthesis that reasons about evidence, addresses contradictions, and
quantifies uncertainty.  Falls back to template formatting on LLM
failure or when no model is configured.
"""

import json as _json
import logging
import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from litellm import acompletion as _litellm_acompletion

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.observability import log_tokens

logger = logging.getLogger("medexpert.report_generator")


DISCLAIMER = (
    "> *This information is for educational purposes only and does not "
    "constitute medical advice. Always consult a qualified healthcare "
    "professional for medical decisions.*"
)


def _format_quick_answer(question: str, evidence: list[dict]) -> str:
    """2-3 sentence answer with top citations."""
    lines = [DISCLAIMER, "", f"**Q: {question}**", ""]

    if not evidence:
        lines.append("No evidence was found for this question.")
        return "\n".join(lines)

    # Use top 3 evidence items
    top = evidence[:3]
    for i, ev in enumerate(top):
        snippet = ev.get("snippet", "No details available.")
        cite_id = ev.get("cite_id", f"s0r{i}")
        lines.append(f"{snippet} [[cite:{cite_id}]]")

    return "\n".join(lines)


def _format_research_brief(
    question: str, evidence: list[dict], verification: dict | None
) -> str:
    """1-page structured summary."""
    lines = [DISCLAIMER, "", f"# Research Brief: {question}", ""]

    # Background
    lines.append("## Background")
    lines.append(f"This brief addresses the question: *{question}*")
    lines.append(f"Based on {len(evidence)} evidence sources.")
    lines.append("")

    # Findings
    lines.append("## Key Findings")
    if not evidence:
        lines.append("No evidence was collected.")
    else:
        for i, ev in enumerate(evidence):
            cite_id = ev.get("cite_id", f"s0r{i}")
            title = ev.get("title", "Untitled")
            snippet = ev.get("snippet", "")
            lines.append(f"- **{title}**: {snippet} [[cite:{cite_id}]]")
    lines.append("")

    # Limitations
    lines.append("## Limitations")
    if len(evidence) < 3:
        lines.append("- Limited evidence base (fewer than 3 sources)")
    if verification and not verification.get("passed", True):
        lines.append("- Some claims could not be fully verified")
    lines.append("- Results should be interpreted in clinical context")
    lines.append("")

    # Conclusion — synthesize evidence instead of generic boilerplate
    lines.append("## Conclusion")
    if not evidence:
        lines.append("Insufficient evidence to draw conclusions.")
    elif len(evidence) == 1:
        title = evidence[0].get("title", "the available source")
        cite_id = evidence[0].get("cite_id", "s0r0")
        lines.append(
            f"Based on a single source ({title} [[cite:{cite_id}]]), "
            "findings should be interpreted with caution. Consult current "
            "clinical guidelines for definitive recommendations."
        )
    else:
        # Summarize the evidence themes with citations
        cite_refs = " ".join(
            f"[[cite:{ev.get('cite_id', f's0r{i}')}]]"
            for i, ev in enumerate(evidence[:3])
        )
        lines.append(
            f"Based on {len(evidence)} sources {cite_refs}, "
            "the evidence supports the key findings described above. "
            "Clinical decisions should integrate these findings with "
            "patient-specific factors and current practice guidelines."
        )

    return "\n".join(lines)


def _format_full_synthesis(
    question: str,
    evidence: list[dict],
    verification: dict | None,
) -> str:
    """Multi-section report with evidence table and methodology notes."""
    lines = [DISCLAIMER, "", f"# Full Evidence Synthesis: {question}", ""]

    # Executive Summary
    lines.append("## Executive Summary")
    lines.append(f"Comprehensive synthesis based on {len(evidence)} evidence sources.")
    lines.append("")

    # Evidence Table
    lines.append("## Evidence Summary Table")
    lines.append("")
    lines.append("| # | Source | Title | Study Type | Grade |")
    lines.append("|---|--------|-------|------------|-------|")
    for i, ev in enumerate(evidence):
        cite_id = ev.get("cite_id", f"s0r{i}")
        source = ev.get("source", "unknown")
        title = ev.get("title", "Untitled")
        study_type = ev.get("study_type", "N/A")
        grade = ev.get("grade", "N/A")
        lines.append(
            f"| {i + 1} | {source} | {title} [[cite:{cite_id}]] | {study_type} | {grade} |"
        )
    lines.append("")

    # Detailed Findings
    lines.append("## Detailed Findings")
    for i, ev in enumerate(evidence):
        cite_id = ev.get("cite_id", f"s0r{i}")
        title = ev.get("title", "Untitled")
        snippet = ev.get("snippet", "")
        lines.append(f"### {i + 1}. {title}")
        lines.append(f"{snippet} [[cite:{cite_id}]]")
        lines.append("")

    # Methodology Notes
    lines.append("## Methodology")
    lines.append("Evidence was graded using the GRADE methodology framework.")
    sources_used = {ev.get("source", "unknown") for ev in evidence}
    lines.append(f"Sources queried: {', '.join(sorted(sources_used))}")
    lines.append("")

    # Verification
    if verification:
        lines.append("## Verification Results")
        score = verification.get("overall_score", "N/A")
        lines.append(f"Overall verification score: {score}")
        unsupported = verification.get("unsupported_claims", [])
        if unsupported:
            lines.append("### Unsupported Claims")
            for claim in unsupported:
                lines.append(f"- {claim}")
        lines.append("")

    return "\n".join(lines)


def _format_advisory_board_report(
    question: str,
    evidence: list[dict],
    advisory_perspectives: list[dict] | None,
    verification: dict | None,
) -> str:
    """Full synthesis + advisory perspectives + consensus analysis."""
    # Start with full synthesis
    report = _format_full_synthesis(question, evidence, verification)
    lines = [report, ""]

    # Advisory Board Section
    lines.append("## Advisory Board Perspectives")
    lines.append("")

    if not advisory_perspectives:
        lines.append("No advisory board perspectives were collected.")
    else:
        for p in advisory_perspectives:
            persona = p.get("persona", "Unknown")
            text = p.get("perspective_text", "")
            lines.append(f"### {persona}")
            lines.append(text)
            lines.append("")

    # Consensus Analysis
    lines.append("## Consensus Analysis")
    if advisory_perspectives and len(advisory_perspectives) >= 2:
        lines.append(
            f"Analysis based on {len(advisory_perspectives)} advisory board perspectives."
        )
    else:
        lines.append("Insufficient perspectives for consensus analysis.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM narrative synthesis
# ---------------------------------------------------------------------------

_SYNTHESIS_PROMPT_TEMPLATE = (
    "Write a research synthesis answering this question. You MUST:\n\n"
    "1. REASON about the evidence — don't just list findings. What do they MEAN together?\n"
    "2. ADDRESS contradictions — if studies disagree, explain why and which is more reliable.\n"
    "3. QUANTIFY uncertainty — use language like 'strong evidence suggests', 'limited data\n"
    "   indicates', 'conflicting results make it unclear whether'.\n"
    "4. CITE sources — use [[cite:ID]] format for every factual claim.\n"
    "5. IDENTIFY the bottom line — after all nuance, what is the best answer we can give?\n\n"
    "QUESTION: {question}\n\n"
    "EVIDENCE:\n{evidence}\n\n"
    "VERIFICATION SCORE: {verification_score}\n\n"
    "Write 3-5 paragraphs. Lead with the strongest conclusion. End with honest limitations.\n"
    "Return ONLY the synthesis text (markdown). Do NOT wrap in JSON."
)


async def _llm_synthesize_narrative(
    question: str,
    evidence: list[dict],
    verification: dict | None,
    model: str,
    temperature: float,
    session_id: str,
    vertex_kwargs: dict | None = None,
) -> str | None:
    """Use LLM to write a narrative synthesis of the evidence.

    Returns the synthesis text on success, or ``None`` on failure.
    """
    ev_lines = []
    for i, ev in enumerate(evidence[:15]):
        cite_id = ev.get("cite_id", f"s0r{i}")
        title = ev.get("title", "Untitled")[:120]
        snippet = (ev.get("snippet", "") or "")[:200]
        grade = ev.get("grade", "N/A")
        ev_lines.append(f"{i + 1}. [{grade}] {title} [[cite:{cite_id}]]: {snippet}")

    v_score = str(verification.get("overall_score", "N/A")) if verification else "N/A"

    prompt = (
        _SYNTHESIS_PROMPT_TEMPLATE
        .replace("{question}", question[:500])
        .replace("{evidence}", "\n".join(ev_lines))
        .replace("{verification_score}", v_score)
    )

    try:
        response = await _litellm_acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=4096,
            **(vertex_kwargs or {}),
        )
        content = response.choices[0].message.content.strip()

        # Token logging
        usage = getattr(response, "usage", None)
        if usage:
            log_tokens(
                session_id=session_id,
                model=model,
                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            )

        if not content:
            return None

        # Strip markdown fences if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:markdown|md|json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        return content

    except Exception as exc:
        logger.warning(
            "[report_generator] LLM narrative synthesis failed, falling "
            "back to template: %s",
            exc,
        )
        return None


class ReportGeneratorTool(DynamicTool):
    """Generates structured research reports in multiple formats."""

    def __init__(self, tool_config: Optional[dict] = None, **kwargs):
        super().__init__(tool_config=tool_config, **kwargs)
        cfg = tool_config or {}
        raw_model = cfg.get("model")
        if isinstance(raw_model, dict):
            self.model: str = raw_model.get("model", "")
            self._vertex_kwargs: dict = {
                k: raw_model[k]
                for k in ("vertex_project", "vertex_location")
                if k in raw_model
            }
        elif raw_model:
            self.model = raw_model
            self._vertex_kwargs = {}
        else:
            self.model = ""
            self._vertex_kwargs = {}
        self.temperature: float = float(cfg.get("temperature", 0.3))

    @property
    def tool_name(self) -> str:
        return "report_generator"

    @property
    def tool_description(self) -> str:
        return (
            "Generates formatted research reports. Modes: quick_answer (2-3 sentences), "
            "research_brief (1-page summary), full_synthesis (multi-section with evidence "
            "table), advisory_board_report (full synthesis + advisory perspectives)."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "mode": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Report mode: quick_answer, research_brief, full_synthesis, advisory_board_report",
                ),
                "question": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The research question being answered",
                ),
                "evidence_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON string of evidence items array",
                ),
                "advisory_perspectives_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON string of advisory perspectives array (for advisory_board_report mode)",
                    nullable=True,
                ),
                "verification_result_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON string of verification result (for full_synthesis and advisory_board_report)",
                    nullable=True,
                ),
            },
            required=["mode", "question", "evidence_json"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        mode = args.get("mode", "").lower().strip()
        question = args.get("question", "").strip()

        valid_modes = ("quick_answer", "research_brief", "full_synthesis", "advisory_board_report")
        if mode not in valid_modes:
            return {"error": f"Invalid mode '{mode}'. Must be one of: {valid_modes}"}

        if not question:
            return {"error": "Question is required"}

        try:
            evidence = _json.loads(args.get("evidence_json", "[]"))
        except _json.JSONDecodeError:
            return {"error": "Invalid JSON in evidence_json"}

        verification = None
        if args.get("verification_result_json"):
            try:
                verification = _json.loads(args["verification_result_json"])
            except _json.JSONDecodeError:
                pass

        advisory = None
        if args.get("advisory_perspectives_json"):
            try:
                advisory = _json.loads(args["advisory_perspectives_json"])
            except _json.JSONDecodeError:
                pass

        # ------------------------------------------------------------------
        # For full_synthesis / advisory_board_report: try LLM narrative
        # ------------------------------------------------------------------
        method = "template"
        if mode == "quick_answer":
            report = _format_quick_answer(question, evidence)
        elif mode == "research_brief":
            report = _format_research_brief(question, evidence, verification)
        elif mode in ("full_synthesis", "advisory_board_report"):
            llm_narrative = None
            if self.model and evidence:
                session_id = ""
                if hasattr(tool_context, "session") and tool_context.session:
                    session_id = getattr(tool_context.session, "id", "")
                llm_narrative = await _llm_synthesize_narrative(
                    question=question,
                    evidence=evidence,
                    verification=verification,
                    model=self.model,
                    temperature=self.temperature,
                    session_id=session_id,
                    vertex_kwargs=self._vertex_kwargs,
                )

            if llm_narrative:
                method = "llm"
                # Build report: disclaimer + LLM narrative + evidence table + advisory
                parts = [DISCLAIMER, "", f"# Evidence Synthesis: {question}", ""]
                parts.append(llm_narrative)
                parts.append("")
                # Append the evidence table for reference
                parts.append("## Evidence Summary Table")
                parts.append("")
                parts.append("| # | Source | Title | Study Type | Grade |")
                parts.append("|---|--------|-------|------------|-------|")
                for i, ev in enumerate(evidence):
                    cite_id = ev.get("cite_id", f"s0r{i}")
                    source = ev.get("source", "unknown")
                    title = ev.get("title", "Untitled")
                    study_type = ev.get("study_type", "N/A")
                    grade = ev.get("grade", "N/A")
                    parts.append(
                        f"| {i + 1} | {source} | {title} [[cite:{cite_id}]] | {study_type} | {grade} |"
                    )
                parts.append("")
                # Verification
                if verification:
                    parts.append("## Verification Results")
                    parts.append(f"Overall verification score: {verification.get('overall_score', 'N/A')}")
                    unsupported = verification.get("unsupported_claims", [])
                    if unsupported:
                        parts.append("### Unsupported Claims")
                        for claim in unsupported:
                            parts.append(f"- {claim}")
                    parts.append("")
                # Advisory (for advisory_board_report mode)
                if mode == "advisory_board_report":
                    parts.append("## Advisory Board Perspectives")
                    parts.append("")
                    if advisory:
                        for p in advisory:
                            persona = p.get("persona", "Unknown")
                            text = p.get("perspective_text", "")
                            parts.append(f"### {persona}")
                            parts.append(text)
                            parts.append("")
                    else:
                        parts.append("No advisory board perspectives were collected.")
                    parts.append("")
                report = "\n".join(parts)
            else:
                # Template fallback
                if mode == "full_synthesis":
                    report = _format_full_synthesis(question, evidence, verification)
                else:
                    report = _format_advisory_board_report(question, evidence, advisory, verification)
        else:
            report = ""

        # Persist final answer to Redis for polling fallback (SSE may drop on Cloud Run)
        if report and mode in ("full_synthesis", "advisory_board_report"):
            try:
                session_id = ""
                if hasattr(tool_context, "session") and tool_context.session:
                    session_id = getattr(tool_context.session, "id", "")
                if session_id:
                    from lifesci_tools.memory_plane import MemoryPlaneTool
                    mp = MemoryPlaneTool._backend
                    if mp is not None:
                        redis_key = f"medexpert:{session_id}:intermediate:final_answer"
                        await mp.set(redis_key, report, ex=3600)
                        logger.info("[report_generator] Stored final_answer to Redis (%d chars)", len(report))
            except Exception as exc:
                logger.warning("[report_generator] Failed to store final_answer to Redis: %s", exc)

        return {
            "report": report,
            "mode": mode,
            "report_mode": mode,
            "evidence_count": len(evidence),
            "has_verification": verification is not None,
            "has_advisory": advisory is not None,
            "method": method,
        }
