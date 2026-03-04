"""Source Collector — publishes MCP-sourced references as RAG citations.

Specialist agents return text with identifiers (PMIDs, NCT IDs, DOIs, etc.)
but no rag_metadata reaches the SSE stream because they run via fire-and-forget
A2A. This tool lets the orchestrator push those references into the citation
pipeline so the frontend Sources panel displays them.

URL construction rules:
  PMID        → https://pubmed.ncbi.nlm.nih.gov/{pmid}/
  NCT ID      → https://clinicaltrials.gov/study/{nct_id}
  DOI         → https://doi.org/{doi}
  explicit URL → used as-is
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool
from solace_agent_mesh.common.rag_dto import create_rag_source, create_rag_search_result

logger = logging.getLogger(__name__)

# ── URL construction helpers ──────────────────────────────────────────

_ID_URL_TEMPLATES = {
    "pmid": "https://pubmed.ncbi.nlm.nih.gov/{id}/",
    "nct_id": "https://clinicaltrials.gov/study/{id}",
    "doi": "https://doi.org/{id}",
}


def _build_url(source: Dict[str, Any]) -> str:
    """Construct a clickable URL from an identifier or explicit url field."""
    if source.get("url"):
        return source["url"]
    for id_type, template in _ID_URL_TEMPLATES.items():
        value = source.get(id_type)
        if value:
            return template.format(id=value)
    return ""


def _source_label(source: Dict[str, Any]) -> str:
    """Human-readable label for the favicon domain extraction."""
    url = _build_url(source)
    if not url:
        return source.get("title", "Unknown source")
    try:
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return url


# ── Inline source reference extraction ────────────────────────────────

# Matches [[pmid:XXXX]], [[nct:NCTXXXX]], [[doi:XXXX]], [[url:XXXX]]
_INLINE_REF_PATTERN = re.compile(r"\[\[(pmid|nct|doi|url):([^\]]+)\]\]")

# Map from inline ref type to the source dict key that holds the ID
_REF_TYPE_TO_SOURCE_KEY = {
    "pmid": "pmid",
    "nct": "nct_id",
    "doi": "doi",
    "url": "url",
}


def _extract_inline_refs(findings: List[str]) -> Set[Tuple[str, str]]:
    """Extract all inline source references from findings strings.

    Returns a set of (ref_type, ref_value) tuples, e.g.
    {("pmid", "38901234"), ("nct", "NCT06123456")}.
    """
    refs: Set[Tuple[str, str]] = set()
    for finding in findings:
        if not isinstance(finding, str):
            continue
        for match in _INLINE_REF_PATTERN.finditer(finding):
            refs.add((match.group(1).lower(), match.group(2).strip()))
    return refs


def _build_source_id_set(sources: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """Build a set of (ref_type, id_value) from the sources array.

    This enables O(1) lookup when cross-referencing inline refs.
    """
    ids: Set[Tuple[str, str]] = set()
    for src in sources:
        for ref_type, src_key in _REF_TYPE_TO_SOURCE_KEY.items():
            value = src.get(src_key)
            if value:
                ids.add((ref_type, str(value).strip()))
    return ids


def cross_reference_findings(
    findings: List[str],
    sources: List[Dict[str, Any]],
) -> List[str]:
    """Cross-reference inline source refs in findings against sources array.

    Returns a list of warning strings for any unmatched references.
    Best-effort: returns empty list if no findings or no refs found.
    """
    if not findings:
        return []

    inline_refs = _extract_inline_refs(findings)
    if not inline_refs:
        return []

    source_ids = _build_source_id_set(sources)
    warnings = []
    for ref_type, ref_value in sorted(inline_refs):
        if (ref_type, ref_value) not in source_ids:
            warnings.append(
                f"Inline reference [[{ref_type}:{ref_value}]] in findings "
                f"has no matching source in the sources array"
            )
    return warnings


class SourceCollectorTool(DynamicTool):
    """Publishes structured references as RAG citations visible in the UI."""

    @property
    def tool_name(self) -> str:
        return "publish_sources"

    @property
    def tool_description(self) -> str:
        return (
            "Publish references from specialist agent responses so they appear "
            "in the Sources sidebar. Call this AFTER collecting specialist results. "
            "Provide a list of sources — each with a title, snippet, and at least "
            "one identifier (pmid, nct_id, doi) or a url. Returns citation IDs "
            "you MUST use in your answer with [[cite:...]] markers.\n\n"
            "Returns citation_map_json — the orchestrator MUST store this verbatim "
            "in memory_plane(namespace='evidence', key='citation_map_json') for the "
            "verifier to use. Without this step, claim verification will be skipped "
            "and return a neutral 0.5 score."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "query": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The original user question (used as the search title).",
                ),
                "sources": adk_types.Schema(
                    type=adk_types.Type.ARRAY,
                    description="List of sources to publish.",
                    items=adk_types.Schema(
                        type=adk_types.Type.OBJECT,
                        properties={
                            "title": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Source title (article name, trial title, etc.).",
                            ),
                            "snippet": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Brief description or key finding.",
                            ),
                            "source_type": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description=(
                                    "Type of source: pubmed, clinical_trial, fda, "
                                    "regulatory, cdc, genomic, environmental, provider, web"
                                ),
                            ),
                            "pmid": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="PubMed ID (e.g. '38901234').",
                            ),
                            "nct_id": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="ClinicalTrials.gov NCT ID (e.g. 'NCT06123456').",
                            ),
                            "doi": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Digital Object Identifier.",
                            ),
                            "url": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Explicit URL (used if no identifier provided).",
                            ),
                            "agent_name": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Name of the agent that provided this source.",
                            ),
                            "mcp_server": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Name of the MCP server used to retrieve this source.",
                            ),
                            "api_endpoint": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="API endpoint that returned this data.",
                            ),
                            "evidence_grade": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="GRADE evidence quality: High, Moderate, Low, Very Low.",
                            ),
                            "study_type": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Study design type: meta_analysis, rct, cohort, case_control, etc.",
                            ),
                        },
                        required=["title", "snippet"],
                    ),
                ),
                "mcp_failures": adk_types.Schema(
                    type=adk_types.Type.ARRAY,
                    description=(
                        "Optional list of MCP server failures from specialist responses. "
                        "Each entry: {server, error_category, is_retryable}."
                    ),
                    items=adk_types.Schema(
                        type=adk_types.Type.OBJECT,
                        properties={
                            "server": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Name of the failed MCP server.",
                            ),
                            "error_category": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Error category: rate_limited, service_unavailable, circuit_open, auth_error, not_found.",
                            ),
                            "is_retryable": adk_types.Schema(
                                type=adk_types.Type.BOOLEAN,
                                description="Whether the failure is transient and retryable.",
                            ),
                        },
                    ),
                ),
                "findings": adk_types.Schema(
                    type=adk_types.Type.ARRAY,
                    description=(
                        "Optional list of finding strings from specialist responses. "
                        "When provided, inline source references ([[pmid:XXXX]], "
                        "[[nct:NCTXXXX]], [[doi:XXXX]], [[url:XXXX]]) are extracted "
                        "and cross-referenced against the sources array. Unmatched "
                        "references are reported in cross_reference_warnings."
                    ),
                    items=adk_types.Schema(
                        type=adk_types.Type.STRING,
                        description="A finding string, potentially containing inline source references.",
                    ),
                ),
            },
            required=["query", "sources"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> Dict[str, Any]:
        log_id = "[publish_sources]"
        query = args.get("query", "")
        sources = args.get("sources") or []

        if not sources:
            mcp_failures = args.get("mcp_failures", [])
            if mcp_failures:
                logger.warning(
                    "%s All sources failed — %d MCP failures reported",
                    log_id,
                    len(mcp_failures),
                )
                return {
                    "status": "all_sources_failed",
                    "mcp_failures": mcp_failures,
                    "valid_citation_ids": [],
                    "aggregate_status": "all_failed",
                }
            logger.warning("%s Called with empty sources list", log_id)
            return {
                "status": "no_sources",
                "valid_citation_ids": [],
                "aggregate_status": "no_evidence",
            }

        # Default evidence grade mapping by source type when not explicitly provided.
        # This ensures citations always have a grade badge in the UI.
        _DEFAULT_GRADES = {
            "pubmed": "Moderate",       # Varies; Moderate as safe default
            "clinical_trial": "High",   # RCTs by design
            "fda": "High",              # Regulatory data
            "regulatory": "High",
            "cdc": "Moderate",          # Surveillance data
            "genomic": "Moderate",
            "environmental": "Low",     # Observational
            "provider": "Low",          # Administrative data
            "web": "Very Low",          # Unreviewed web content
        }

        # Build RAG sources and citation IDs
        rag_sources = []
        valid_citation_ids = []
        now = datetime.now(timezone.utc).isoformat()

        for i, src in enumerate(sources):
            citation_id = f"s0r{i}"
            valid_citation_ids.append(citation_id)

            if not src.get("publication_year"):
                logger.warning(
                    "%s Source '%s' missing publication_year",
                    log_id,
                    src.get("title", "unknown"),
                )

            url = _build_url(src)
            domain = _source_label(src)

            rag_source = create_rag_source(
                citation_id=citation_id,
                file_id=f"mcp_source_{i}",
                filename=domain,
                title=src.get("title", ""),
                source_url=url,
                url=url,
                content_preview=src.get("snippet", "")[:200],
                relevance_score=1.0,
                source_type=src.get("source_type", "reference"),
                retrieved_at=now,
                metadata={
                    "title": src.get("title", ""),
                    "link": url,
                    "type": src.get("source_type", "reference"),
                    "favicon": (
                        f"https://www.google.com/s2/favicons?domain={url}&sz=32"
                        if url
                        else ""
                    ),
                    "agent_name": src.get("agent_name", ""),
                    "mcp_server": src.get("mcp_server", ""),
                    "api_endpoint": src.get("api_endpoint", ""),
                    "evidence_grade": src.get("evidence_grade")
                        or _DEFAULT_GRADES.get(src.get("source_type", ""), ""),
                    "study_type": src.get("study_type", ""),
                    "publication_year": src.get("publication_year"),
                },
            )
            rag_sources.append(rag_source)

        rag_metadata = create_rag_search_result(
            query=query,
            search_type="web_search",
            timestamp=now,
            sources=rag_sources,
        )

        logger.info(
            "%s Published %d sources for query: %s",
            log_id,
            len(rag_sources),
            query[:80],
        )

        # Build citation map in the EXACT format claim_verifier expects.
        # This is programmatic — the orchestrator stores it verbatim in
        # memory_plane for the verifier to retrieve and pass directly.
        citation_map = []
        for i, src in enumerate(sources):
            citation_map.append({
                "id": f"s0r{i}",
                "title": src.get("title", ""),
                "snippet": src.get("snippet", ""),
                "url": _build_url(src),
            })
        citation_map_json = json.dumps(citation_map)

        # Build formatted results for the LLM
        lines = [f"=== PUBLISHED SOURCES ({len(rag_sources)} references) ==="]
        lines.append(f"Query: {query}")
        lines.append(f"Valid citation IDs: {', '.join(valid_citation_ids)}")
        lines.append("")
        for i, src in enumerate(sources):
            cid = f"s0r{i}"
            lines.append(f"--- SOURCE {i + 1} ---")
            lines.append(f"CITATION ID: [[cite:{cid}]]")
            lines.append(f"TITLE: {src.get('title', 'N/A')}")
            lines.append(f"URL: {_build_url(src)}")
            if src.get("agent_name"):
                lines.append(f"AGENT: {src.get('agent_name')}")
            if src.get("mcp_server"):
                lines.append(f"MCP SERVER: {src.get('mcp_server')}")
            lines.append(f"CONTENT: {src.get('snippet', 'N/A')}")
            lines.append(f"USE [[cite:{cid}]] to cite facts from THIS source only")
            lines.append("")
        lines.append("=== END PUBLISHED SOURCES ===")

        # Determine aggregate status based on MCP failures
        mcp_failures = args.get("mcp_failures", [])
        if mcp_failures:
            aggregate_status = "partial"
        else:
            aggregate_status = "all_retrieved"

        # Cross-reference inline source refs in findings against sources.
        # Best-effort: only runs when findings are provided.
        cross_ref_warnings: List[str] = []
        findings = args.get("findings")
        if findings and isinstance(findings, list):
            cross_ref_warnings = cross_reference_findings(findings, sources)
            for warning in cross_ref_warnings:
                logger.warning("%s %s", log_id, warning)

        return {
            "status": "published",
            "formatted_results": "\n".join(lines),
            "rag_metadata": rag_metadata,
            "valid_citation_ids": valid_citation_ids,
            "num_sources": len(rag_sources),
            "citation_map_json": citation_map_json,
            "aggregate_status": aggregate_status,
            "cross_reference_warnings": cross_ref_warnings,
        }
