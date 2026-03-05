"""Graph Writer — persists research session entities to Memgraph.

Called at protocol step 6 (PERSIST) to extract biomedical entities from
session data and write them to Memgraph as nodes and edges.

Entity extraction is regex-based (deterministic, zero LLM calls):
- PMIDs, NCT IDs, DOIs from source references
- Specialist names from session state
- Session metadata (query, domain, timestamp)

Uses Cypher MERGE for idempotent upserts. Never raises — returns
structured error on failure so PERSIST step is never blocked.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

log = logging.getLogger(__name__)

# ── Memgraph connection ───────────────────────────────────────────────

MEMGRAPH_URL = os.environ.get("MEMGRAPH_URL", "")
MEMGRAPH_USER = os.environ.get("MEMGRAPH_USER", "memgraph")
MEMGRAPH_PASSWORD = os.environ.get("MEMGRAPH_PASSWORD", "")
MEMGRAPH_TIMEOUT = 5  # seconds

_driver = None


def _get_rw_driver():
    """Get the read-write Memgraph driver (lazy singleton)."""
    global _driver
    if _driver is not None:
        return _driver

    if not MEMGRAPH_URL:
        return None

    try:
        from neo4j import GraphDatabase

        _driver = GraphDatabase.driver(
            MEMGRAPH_URL,
            auth=(MEMGRAPH_USER, MEMGRAPH_PASSWORD),
            connection_timeout=MEMGRAPH_TIMEOUT,
            max_transaction_retry_time=MEMGRAPH_TIMEOUT,
        )
        _driver.verify_connectivity()
        return _driver
    except Exception as exc:
        log.warning("graph_writer: Memgraph connection failed: %s", exc)
        _driver = None
        return None


# ── Entity extraction (regex, no LLM) ────────────────────────────────

_PMID_RE = re.compile(r"\b(?:PMID|pmid)[:\s]*(\d{6,9})\b")
_NCT_RE = re.compile(r"\b(NCT\d{8})\b", re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;\"']+)\b")
_DRUG_INLINE_RE = re.compile(r"\[\[drug:([^\]]+)\]\]")
_DISEASE_INLINE_RE = re.compile(r"\[\[disease:([^\]]+)\]\]")
_GENE_INLINE_RE = re.compile(r"\[\[gene:([^\]]+)\]\]")


def extract_pmids(text: str) -> list[str]:
    """Extract PubMed IDs from text."""
    return list(set(_PMID_RE.findall(text)))


def extract_nct_ids(text: str) -> list[str]:
    """Extract ClinicalTrials.gov NCT IDs from text."""
    return list(set(m.upper() for m in _NCT_RE.findall(text)))


def extract_dois(text: str) -> list[str]:
    """Extract DOIs from text."""
    return list(set(_DOI_RE.findall(text)))


def extract_inline_entities(text: str) -> dict[str, list[str]]:
    """Extract inline-tagged entities: [[drug:name]], [[disease:name]], [[gene:name]]."""
    return {
        "drugs": list(set(_DRUG_INLINE_RE.findall(text))),
        "diseases": list(set(_DISEASE_INLINE_RE.findall(text))),
        "genes": list(set(_GENE_INLINE_RE.findall(text))),
    }


def extract_entities_from_sources(sources: list[dict]) -> dict[str, list[dict]]:
    """Extract structured entity data from source arrays.

    Parses the structured JSON that specialists return, extracting
    PMIDs, NCT IDs, titles, and publication years.
    """
    studies = []
    seen_ids = set()

    for src in sources:
        pmid = src.get("pmid") or ""
        nct_id = src.get("nct_id") or ""
        doi = src.get("doi") or ""
        title = src.get("title", "")
        year = src.get("publication_year") or src.get("year", "")

        # Deduplicate by primary ID
        primary_id = pmid or nct_id or doi
        if not primary_id or primary_id in seen_ids:
            continue
        seen_ids.add(primary_id)

        studies.append({
            "pmid": str(pmid),
            "nct_id": str(nct_id).upper() if nct_id else "",
            "doi": str(doi),
            "title": str(title)[:500],
            "year": str(year),
        })

    return {"studies": studies}


# ── Cypher generation ─────────────────────────────────────────────────


def build_session_cypher(
    session_id: str,
    query_text: str,
    domain: str,
    specialists_used: list[str],
    studies: list[dict],
    diseases: list[str],
    drugs: list[str],
    genes: list[str],
) -> list[tuple[str, dict]]:
    """Build a list of (cypher, params) tuples for the session graph.

    Uses MERGE for idempotent writes.
    """
    statements = []
    now = datetime.now(timezone.utc).isoformat()

    # 1. Session node
    statements.append((
        "MERGE (s:Session {session_id: $sid}) "
        "ON CREATE SET s.query = $query, s.domain = $domain, s.created_at = $now "
        "ON MATCH SET s.query = $query",
        {"sid": session_id, "query": query_text[:1000], "domain": domain[:200], "now": now},
    ))

    # 2. Specialist nodes + QUERIED edges
    for spec in specialists_used:
        statements.append((
            "MERGE (sp:Specialist {name: $name}) "
            "WITH sp "
            "MATCH (s:Session {session_id: $sid}) "
            "MERGE (s)-[:QUERIED]->(sp)",
            {"name": spec, "sid": session_id},
        ))

    # 3. Study nodes + CITED edges
    # Use the correct identifier type as the MERGE key to avoid duplicates
    for study in studies:
        if study.get("pmid"):
            merge_prop = "pmid"
            merge_val = study["pmid"]
        elif study.get("nct_id"):
            merge_prop = "nct_id"
            merge_val = study["nct_id"]
        elif study.get("doi"):
            merge_prop = "doi"
            merge_val = study["doi"]
        else:
            continue
        statements.append((
            f"MERGE (st:Study {{{merge_prop}: $primary_id}}) "
            "ON CREATE SET st.pmid = $pmid, st.nct_id = $nct_id, st.doi = $doi, "
            "st.title = $title, st.year = $year, st.created_at = $now "
            "WITH st "
            "MATCH (s:Session {session_id: $sid}) "
            "MERGE (s)-[:CITED]->(st)",
            {
                "primary_id": merge_val,
                "pmid": study.get("pmid", ""),
                "nct_id": study.get("nct_id", ""),
                "doi": study.get("doi", ""),
                "title": study.get("title", ""),
                "year": study.get("year", ""),
                "now": now,
                "sid": session_id,
            },
        ))

    # 4. Disease nodes + ABOUT edges
    for disease in diseases:
        statements.append((
            "MERGE (d:Disease {name: $name}) "
            "ON CREATE SET d.created_at = $now "
            "WITH d "
            "MATCH (s:Session {session_id: $sid}) "
            "MERGE (s)-[:ABOUT]->(d)",
            {"name": disease[:200], "now": now, "sid": session_id},
        ))

    # 5. Drug nodes + ABOUT edges
    for drug in drugs:
        statements.append((
            "MERGE (dr:Drug {name: $name}) "
            "ON CREATE SET dr.created_at = $now "
            "WITH dr "
            "MATCH (s:Session {session_id: $sid}) "
            "MERGE (s)-[:ABOUT]->(dr)",
            {"name": drug[:200], "now": now, "sid": session_id},
        ))

    # 6. Gene nodes + ABOUT edges
    for gene in genes:
        statements.append((
            "MERGE (g:Gene {name: $name}) "
            "ON CREATE SET g.created_at = $now "
            "WITH g "
            "MATCH (s:Session {session_id: $sid}) "
            "MERGE (s)-[:ABOUT]->(g)",
            {"name": gene[:100], "now": now, "sid": session_id},
        ))

    return statements


# ── Redis data reader ─────────────────────────────────────────────────


async def _read_session_data(session_id: str) -> dict[str, Any]:
    """Read specialist findings from Redis memory plane directly.

    Falls back to empty data if Redis is unavailable.
    """
    data = {
        "specialists_used": [],
        "query_text": "",
        "domain": "",
        "sources": [],
        "findings_text": "",
    }

    try:
        import redis.asyncio as aioredis

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        client = aioredis.from_url(redis_url, decode_responses=True)

        try:
            # Key prefix matches memory_plane._make_key(): "medexpert:{sid}:{ns}:{key}"
            pfx = f"medexpert:{session_id}"

            # Read key session signals
            specs = await client.get(f"{pfx}:intermediate:specialists_used")
            if specs:
                data["specialists_used"] = json.loads(specs) if isinstance(specs, str) else specs

            domain = await client.get(f"{pfx}:intermediate:query_domain")
            if domain:
                data["domain"] = domain

            query = await client.get(f"{pfx}:intermediate:query_text")
            if query:
                data["query_text"] = query

            # Scan for source data across namespaces
            sources_raw = await client.get(f"{pfx}:citations:published_sources")
            if sources_raw:
                try:
                    data["sources"] = json.loads(sources_raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Collect all findings text for entity extraction
            findings_parts = []
            async for key in client.scan_iter(match=f"{pfx}:evidence:*", count=50):
                val = await client.get(key)
                if val:
                    findings_parts.append(val)
            data["findings_text"] = "\n".join(findings_parts)

        finally:
            await client.aclose()

    except ImportError:
        log.warning("graph_writer: redis package not available, using provided params only")
    except Exception as exc:
        log.warning("graph_writer: Redis read failed (non-fatal): %s", exc)

    return data


# ── DynamicTool implementation ────────────────────────────────────────


class GraphWriterTool(DynamicTool):
    """Persists research session entities to the Memgraph knowledge graph."""

    @property
    def tool_name(self) -> str:
        return "graph_writer"

    @property
    def tool_description(self) -> str:
        return (
            "Persist research session entities to the knowledge graph (Memgraph). "
            "Called at STEP 6 (PERSIST) to extract biomedical entities from session "
            "data and write them as nodes and edges. Uses MERGE for idempotent writes.\n\n"
            "Extracts: PMIDs, NCT IDs, DOIs, specialist names, diseases, drugs, genes.\n"
            "Creates: Session, Specialist, Study, Disease, Drug, Gene nodes.\n"
            "Links: QUERIED, CITED, ABOUT, MENTIONS edges.\n\n"
            "Parameters:\n"
            "- session_id (required): current session identifier\n"
            "- query_text: the original user query\n"
            "- domain: research domain (e.g., 'oncology', 'cardiology')\n"
            "- specialists_used: JSON array of specialist names used\n"
            "- sources: JSON array of source objects with pmid/nct_id/doi/title/year\n"
            "- findings_text: combined specialist findings text for entity extraction\n\n"
            "If parameters are omitted, the tool reads from the Redis memory plane. "
            "Never fails — returns structured error if Memgraph is unavailable."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "session_id": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Current session identifier (required)",
                ),
                "query_text": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The original user query",
                    nullable=True,
                ),
                "domain": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Research domain (e.g., oncology, cardiology)",
                    nullable=True,
                ),
                "specialists_used": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON array of specialist agent names used",
                    nullable=True,
                ),
                "sources": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON array of source objects [{pmid, nct_id, doi, title, year}]",
                    nullable=True,
                ),
                "findings_text": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Combined specialist findings text for entity extraction",
                    nullable=True,
                ),
            },
            required=["session_id"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict:
        """Execute the graph write operation. Never raises."""
        try:
            return await self._do_write(args, tool_context)
        except Exception as exc:
            log.error("graph_writer: unexpected error: %s", exc, exc_info=True)
            return {
                "success": False,
                "error": str(exc),
                "error_category": "graph_unavailable",
                "is_retryable": True,
                "nodes_created": 0,
                "edges_created": 0,
            }

    async def _do_write(self, args: dict, tool_context: ToolContext) -> dict:
        """Core write logic."""
        session_id = args.get("session_id", "")
        if not session_id:
            # Try to get from tool context
            if hasattr(tool_context, "session") and tool_context.session:
                session_id = getattr(tool_context.session, "id", "")
            if not session_id:
                return {
                    "success": False,
                    "error": "session_id is required",
                    "error_category": "validation_error",
                    "is_retryable": False,
                    "nodes_created": 0,
                    "edges_created": 0,
                }

        # Get driver
        driver = _get_rw_driver()
        if driver is None:
            return {
                "success": False,
                "error": "Memgraph not available",
                "error_category": "graph_unavailable",
                "is_retryable": True,
                "nodes_created": 0,
                "edges_created": 0,
            }

        # Collect data — from args or Redis fallback
        query_text = args.get("query_text", "")
        domain = args.get("domain", "")
        specialists_used = []
        sources = []
        findings_text = args.get("findings_text", "")

        # Parse JSON string params
        specs_raw = args.get("specialists_used", "")
        if specs_raw:
            try:
                specialists_used = json.loads(specs_raw) if isinstance(specs_raw, str) else specs_raw
            except (json.JSONDecodeError, TypeError):
                pass

        sources_raw = args.get("sources", "")
        if sources_raw:
            try:
                sources = json.loads(sources_raw) if isinstance(sources_raw, str) else sources_raw
            except (json.JSONDecodeError, TypeError):
                pass

        # Supplement from Redis if data is sparse
        if not specialists_used or not query_text:
            redis_data = await _read_session_data(session_id)
            if not specialists_used:
                specialists_used = redis_data.get("specialists_used", [])
            if not query_text:
                query_text = redis_data.get("query_text", "")
            if not domain:
                domain = redis_data.get("domain", "")
            if not sources:
                sources = redis_data.get("sources", [])
            if not findings_text:
                findings_text = redis_data.get("findings_text", "")

        # Extract entities
        extracted_sources = extract_entities_from_sources(sources)
        studies = extracted_sources["studies"]

        # Extract from findings text
        all_text = findings_text + " " + query_text
        text_pmids = extract_pmids(all_text)
        text_nct_ids = extract_nct_ids(all_text)
        inline = extract_inline_entities(all_text)

        # Add text-extracted PMIDs as studies (if not already in source list)
        existing_pmids = {s["pmid"] for s in studies if s.get("pmid")}
        for pmid in text_pmids:
            if pmid not in existing_pmids:
                studies.append({"pmid": pmid, "nct_id": "", "doi": "", "title": "", "year": ""})

        existing_ncts = {s["nct_id"] for s in studies if s.get("nct_id")}
        for nct in text_nct_ids:
            if nct not in existing_ncts:
                studies.append({"pmid": "", "nct_id": nct, "doi": "", "title": "", "year": ""})

        diseases = inline.get("diseases", [])
        drugs = inline.get("drugs", [])
        genes = inline.get("genes", [])

        # Build and execute Cypher statements
        statements = build_session_cypher(
            session_id=session_id,
            query_text=query_text,
            domain=domain,
            specialists_used=specialists_used,
            studies=studies,
            diseases=diseases,
            drugs=drugs,
            genes=genes,
        )

        nodes_created = 0
        edges_created = 0
        errors = []

        with driver.session() as db_session:
            for cypher, params in statements:
                try:
                    result = db_session.run(cypher, params)
                    summary = result.consume()
                    nodes_created += summary.counters.nodes_created
                    edges_created += summary.counters.relationships_created
                except Exception as exc:
                    errors.append(str(exc)[:200])
                    log.warning("graph_writer: statement failed: %s", exc)

        return {
            "success": len(errors) == 0,
            "nodes_created": nodes_created,
            "edges_created": edges_created,
            "total_statements": len(statements),
            "errors": errors[:5] if errors else [],
            "entities_found": {
                "specialists": len(specialists_used),
                "studies": len(studies),
                "diseases": len(diseases),
                "drugs": len(drugs),
                "genes": len(genes),
            },
        }
