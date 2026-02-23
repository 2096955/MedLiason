"""Report Generator — multi-format output for research results.

Supports 4 output modes: quick_answer, research_brief, full_synthesis,
and advisory_board_report. Each mode produces a structured markdown
document with citation markers.
"""

import json
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool


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

    # Conclusion
    lines.append("## Conclusion")
    if evidence:
        lines.append(
            f"Based on {len(evidence)} sources, preliminary findings are presented above. "
            "Further investigation may be warranted."
        )
    else:
        lines.append("Insufficient evidence to draw conclusions.")

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


class ReportGeneratorTool(DynamicTool):
    """Generates structured research reports in multiple formats."""

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
            evidence = json.loads(args.get("evidence_json", "[]"))
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in evidence_json"}

        verification = None
        if args.get("verification_result_json"):
            try:
                verification = json.loads(args["verification_result_json"])
            except json.JSONDecodeError:
                pass

        advisory = None
        if args.get("advisory_perspectives_json"):
            try:
                advisory = json.loads(args["advisory_perspectives_json"])
            except json.JSONDecodeError:
                pass

        if mode == "quick_answer":
            report = _format_quick_answer(question, evidence)
        elif mode == "research_brief":
            report = _format_research_brief(question, evidence, verification)
        elif mode == "full_synthesis":
            report = _format_full_synthesis(question, evidence, verification)
        elif mode == "advisory_board_report":
            report = _format_advisory_board_report(question, evidence, advisory, verification)
        else:
            report = ""

        return {
            "report": report,
            "mode": mode,
            "evidence_count": len(evidence),
            "has_verification": verification is not None,
            "has_advisory": advisory is not None,
        }
