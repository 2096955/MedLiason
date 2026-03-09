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
import threading
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
_driver_lock = threading.Lock()


def _get_rw_driver():
    """Get the read-write Memgraph driver (thread-safe lazy singleton)."""
    global _driver
    if _driver is not None:
        return _driver

    if not MEMGRAPH_URL:
        return None

    with _driver_lock:
        # Double-checked locking: re-check after acquiring lock
        if _driver is not None:
            return _driver

        try:
            from neo4j import GraphDatabase

            drv = GraphDatabase.driver(
                MEMGRAPH_URL,
                auth=(MEMGRAPH_USER, MEMGRAPH_PASSWORD),
                connection_timeout=MEMGRAPH_TIMEOUT,
                max_transaction_retry_time=MEMGRAPH_TIMEOUT,
            )
            drv.verify_connectivity()
            _driver = drv
            return _driver
        except Exception as exc:
            log.warning("graph_writer: Memgraph connection failed: %s", exc)
            return None


# ── Entity extraction (regex, no LLM) ────────────────────────────────

_PMID_RE = re.compile(r"\b(?:PMID|pmid)[:\s]*(\d{6,9})\b")
_NCT_RE = re.compile(r"\b(NCT\d{8})\b", re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;\"']+)\b")
_DRUG_INLINE_RE = re.compile(r"\[\[drug:([^\]]+)\]\]")
_DISEASE_INLINE_RE = re.compile(r"\[\[disease:([^\]]+)\]\]")
_GENE_INLINE_RE = re.compile(r"\[\[gene:([^\]]+)\]\]")

# ── Module-level constants for entity extraction ─────────────────────

# Pharmaceutical name suffixes as (suffix, min_word_length) tuples.
# Short stems like "sone" require longer words to avoid false positives
# (e.g. "ozone" matches "sone" at 5 chars — require 7+).
_DRUG_SUFFIX_RULES: tuple[tuple[str, int], ...] = (
    # INN stems (safe at 4-char word minimum)
    ("mab", 4), ("nib", 5), ("vir", 5), ("statin", 6), ("pril", 5),
    ("sartan", 6), ("olol", 5), ("oxacin", 7), ("cillin", 7), ("mycin", 6),
    ("cycline", 8), ("azole", 6), ("prazole", 8), ("gliptin", 8),
    ("glutide", 8), ("gliflozin", 10), ("fenac", 6), ("profen", 7),
    ("coxib", 6), ("setron", 7),
    # Corticosteroids (min-length prevents short false positives)
    ("sone", 6), ("olone", 7), ("onide", 7),
    ("propionate", 11), ("valerate", 9), ("acetonide", 10),
    ("butyrate", 9), ("dipropionate", 14),
    # Benzodiazepines (require 7+ to avoid "clam", "spam", "slam")
    ("zepam", 7),
    # PDE5 inhibitors (-afil stem covers sildenafil, tadalafil, vardenafil)
    ("afil", 7),
    # Leukotriene antagonists
    ("lukast", 8),
    # Calcium channel blockers
    ("dipine", 7),
)

# Drug-class terms that should NOT be classified as diseases or drugs
_DRUG_CLASS_EXCLUSIONS = frozenset({
    "corticosteroid", "corticosteroids", "antibiotic", "antibiotics",
    "nsaid", "nsaids", "antidepressant", "antidepressants",
    "antihistamine", "antihistamines", "benzodiazepine", "benzodiazepines",
    "steroid", "steroids", "analgesic", "analgesics",
    "antipyretic", "antipyretics", "antiviral", "antivirals",
    "antifungal", "antifungals", "anticoagulant", "anticoagulants",
    "immunosuppressant", "immunosuppressants", "bronchodilator",
    "bronchodilators", "diuretic", "diuretics", "laxative", "laxatives",
    "antiemetic", "antiemetics", "anticonvulsant", "anticonvulsants",
    "beta-blocker", "beta-blockers", "ace-inhibitor", "ace-inhibitors",
    "statin", "statins", "insulin", "metformin", "aspirin",
    # Salt / ester forms — not drugs themselves, part of compound names
    "dipropionate", "propionate", "valerate", "acetonide",
    "butyrate", "furoate", "hemisuccinate", "phosphate",
    "hydrochloride", "sulfate", "maleate", "fumarate",
    "citrate", "tartrate", "mesylate", "besylate",
})

# Gene pattern: 2-5 uppercase letters + optional 0-3 alphanumeric (max 8 chars)
# Matches: BRCA1, IL4R, CYP3A5, TP53, HER2, EGFR, KRAS, KCNQ1
_GENE_RE = re.compile(r"\b([A-Z]{2,5}[A-Z0-9]{0,3})\b")

_GENE_EXCLUSIONS = frozenset({
    # Common abbreviations
    "PMID", "NCT", "DOI", "FDA", "CDC", "WHO", "NHS", "URL",
    # English stopwords (all-caps in titles)
    "THE", "AND", "FOR", "WITH", "FROM", "THAT", "THIS", "NOT",
    "ARE", "WAS", "BUT", "HAS", "HAD", "MAY", "CAN", "ALL",
    "ANY", "ITS", "OUR", "YOU", "USE", "ONE", "TWO", "NEW",
    "OLD", "SET", "GET", "PUT", "RUN", "END", "TOP", "OUT",
    # Medical abbreviations (not gene names)
    "HIV", "BMI", "ICU", "MRI", "DNA", "RNA", "API", "SSE",
    "JSON", "HTTP", "POST", "USA", "ATP", "PCR", "RCT", "AUC",
    "SNP", "QOL", "HBA", "LDL", "HDL", "BMD", "CVD", "CVA",
    "AMI", "CHD", "COPD", "EHR", "EMR", "GFR", "BUN",
    # Source title noise
    "RESULTS", "METHODS", "STUDY", "TRIAL",
    "REVIEW", "META", "DATA", "GROUP",
    "DOSE", "RISK", "RATE", "CASE", "CARE",
})

# Stopwords for disease extraction
_DISEASE_STOPWORDS = frozenset({
    "the", "this", "that", "what", "which", "some", "many", "most",
    "educational", "purposes", "information", "professional",
})

# Disease patterns (compiled once at module level)
_DISEASE_PATTERN_PREPOSITIONAL = re.compile(
    r"(?:for|of|in|treat(?:ment)?|with)\s+"
    r"([A-Za-z][A-Za-z-]+(?:\s+[A-Za-z-]+){0,4}"
    r"(?:\s+(?:disease|syndrome|cancer|disorder|infection|failure|deficiency|anemia|anaemia))?)",
    re.IGNORECASE,
)
_DISEASE_PATTERN_DIRECT = re.compile(
    r"\b((?:type\s+[12]\s+)?(?:diabetes|hypertension|asthma|copd|"
    r"alzheimer(?:'s)?|parkinson(?:'s)?|epilepsy|"
    r"leukemia|leukaemia|lymphoma|melanoma|carcinoma|sarcoma|"
    r"arthritis|osteoporosis|fibromyalgia|psoriasis|eczema|"
    r"schizophrenia|depression|anxiety|migraine|"
    r"pneumonia|tuberculosis|hepatitis|cirrhosis|"
    r"multiple\s+sclerosis|crohn(?:'s)?|lupus|gout))\b",
    re.IGNORECASE,
)


def _is_drug_suffix_match(word_lower: str) -> bool:
    """Check if a word matches a pharmaceutical suffix with min-length guard."""
    for sfx, min_len in _DRUG_SUFFIX_RULES:
        if word_lower.endswith(sfx) and len(word_lower) >= min_len:
            return True
    return False


def extract_entities_from_source_metadata(sources: list[dict], query_text: str) -> dict[str, list[str]]:
    """Extract drug/disease/gene names from source metadata fields.

    Specialists populate ``agent_name``, ``source_type``, and ``title`` on
    every source.  This function uses those structured fields — plus the
    user query — to find entity names that inline tags miss.

    Strategy:
    - Drug sources (source_type contains 'drug'/'fda') → extract drug names
      from title using known pharmaceutical suffixes (with min-word-length guard)
    - Disease entities → extracted from query text using common patterns
      (supports hyphens, e.g. "non-small cell lung cancer")
    - Gene entities → extracted from genomic sources using gene name patterns
      (supports mid-string digits like IL4R, CYP3A5, capped at 8 chars)
    """
    drugs: set[str] = set()
    diseases: set[str] = set()
    genes: set[str] = set()

    # Extract from source titles based on source_type/agent_name
    for src in sources:
        title = (src.get("title") or "").strip()
        agent = (src.get("agent_name") or "").lower()
        stype = (src.get("source_type") or "").lower()
        snippet = (src.get("snippet") or "")
        text_block = title + " " + snippet

        # Drug extraction from drug/FDA sources
        if "drug" in agent or "drug" in stype or "fda" in stype or "pharm" in stype:
            for word in re.findall(r"\b[A-Za-z]{4,}\b", text_block):
                lower = word.lower()
                if lower not in _DRUG_CLASS_EXCLUSIONS and _is_drug_suffix_match(lower):
                    drugs.add(word.capitalize())

        # Gene extraction from genomic sources
        if "genom" in agent or "genom" in stype or "gene" in stype:
            for match in _GENE_RE.findall(text_block):
                if len(match) >= 2 and match not in _GENE_EXCLUSIONS:
                    genes.add(match)

    # Also scan query text for drug names (user may mention drugs directly)
    for word in re.findall(r"\b[A-Za-z]{4,}\b", query_text):
        lower = word.lower()
        if lower not in _DRUG_CLASS_EXCLUSIONS and _is_drug_suffix_match(lower):
            drugs.add(word.capitalize())

    # ── Disease extraction from query text ────────────────────────────
    for pat in (_DISEASE_PATTERN_PREPOSITIONAL, _DISEASE_PATTERN_DIRECT):
        for match in pat.findall(query_text):
            cleaned = match.strip().rstrip("?.,!").strip()
            if len(cleaned) > 3:
                # Token-level exclusion: reject if ANY word is a drug-class term
                tokens = set(cleaned.lower().split())
                if tokens & _DRUG_CLASS_EXCLUSIONS:
                    continue
                if tokens & _DISEASE_STOPWORDS:
                    continue
                diseases.add(cleaned)

    return {
        "drugs": list(drugs),
        "diseases": list(diseases),
        "genes": list(genes),
    }


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
    *,
    specialist_entities: dict[str, dict[str, list[str]]] | None = None,
    entity_studies: dict[str, list[str]] | None = None,
    cross_references: list[tuple[str, str]] | None = None,
) -> list[tuple[str, dict]]:
    """Build a list of (cypher, params) tuples for the session graph.

    Uses MERGE for idempotent writes.

    New DAG edge model (when attribution maps are provided):
        Session ──QUERIED──► Specialist ──FOUND──► Entity ──EVIDENCED_BY──► Study
                                                            Study ──CITED──► Study

    When ``specialist_entities`` is None the function falls back to the legacy
    star topology (Session ──ABOUT──► Entity) so callers that have not been
    updated continue to produce a reasonable graph.
    """
    statements = []
    now = datetime.now(timezone.utc).isoformat()

    # Determine whether we have DAG attribution data
    _has_dag_data = bool(specialist_entities)

    # 1. Session node
    statements.append((
        "MERGE (s:Session {session_id: $sid}) "
        "ON CREATE SET s.query = $query, s.domain = $domain, s.created_at = $now "
        "ON MATCH SET s.query = $query",
        {"sid": session_id, "query": query_text[:1000], "domain": domain[:200], "now": now},
    ))

    # 2. Specialist nodes + Session-QUERIED->Specialist edges
    for spec in specialists_used:
        statements.append((
            "MERGE (sp:Specialist {name: $name}) "
            "WITH sp "
            "MATCH (s:Session {session_id: $sid}) "
            "MERGE (s)-[:QUERIED]->(sp)",
            {"name": spec, "sid": session_id},
        ))

    # 3. Study nodes (no Session→Study edges — studies are reached via entities)
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
        partial = study.get("partial", False)
        statements.append((
            f"MERGE (st:Study {{{merge_prop}: $primary_id}}) "
            "ON CREATE SET st.pmid = $pmid, st.nct_id = $nct_id, st.doi = $doi, "
            "st.title = $title, st.year = $year, st.partial = $partial, st.created_at = $now",
            {
                "primary_id": merge_val,
                "pmid": study.get("pmid", ""),
                "nct_id": study.get("nct_id", ""),
                "doi": study.get("doi", ""),
                "title": study.get("title", ""),
                "year": study.get("year", ""),
                "partial": partial,
                "now": now,
            },
        ))

    # 4. Entity nodes (Disease, Drug, Gene)
    # When DAG data is available → create nodes only (edges via Specialist-FOUND->Entity)
    # When DAG data is missing  → fallback to legacy Session-ABOUT->Entity edges
    for disease in diseases:
        if _has_dag_data:
            statements.append((
                "MERGE (d:Disease {name: $name}) "
                "ON CREATE SET d.created_at = $now",
                {"name": disease[:200], "now": now},
            ))
        else:
            statements.append((
                "MERGE (d:Disease {name: $name}) "
                "ON CREATE SET d.created_at = $now "
                "WITH d "
                "MATCH (s:Session {session_id: $sid}) "
                "MERGE (s)-[:ABOUT]->(d)",
                {"name": disease[:200], "now": now, "sid": session_id},
            ))

    for drug in drugs:
        if _has_dag_data:
            statements.append((
                "MERGE (dr:Drug {name: $name}) "
                "ON CREATE SET dr.created_at = $now",
                {"name": drug[:200], "now": now},
            ))
        else:
            statements.append((
                "MERGE (dr:Drug {name: $name}) "
                "ON CREATE SET dr.created_at = $now "
                "WITH dr "
                "MATCH (s:Session {session_id: $sid}) "
                "MERGE (s)-[:ABOUT]->(dr)",
                {"name": drug[:200], "now": now, "sid": session_id},
            ))

    for gene in genes:
        if _has_dag_data:
            statements.append((
                "MERGE (g:Gene {name: $name}) "
                "ON CREATE SET g.created_at = $now",
                {"name": gene[:100], "now": now},
            ))
        else:
            statements.append((
                "MERGE (g:Gene {name: $name}) "
                "ON CREATE SET g.created_at = $now "
                "WITH g "
                "MATCH (s:Session {session_id: $sid}) "
                "MERGE (s)-[:ABOUT]->(g)",
                {"name": gene[:100], "now": now, "sid": session_id},
            ))

    # 5. Specialist -[:FOUND]-> Entity edges (DAG model only)
    if specialist_entities:
        for spec_name, ent_map in specialist_entities.items():
            for disease in ent_map.get("diseases", []):
                statements.append((
                    "MATCH (sp:Specialist {name: $spec}) "
                    "MATCH (d:Disease {name: $name}) "
                    "MERGE (sp)-[:FOUND]->(d)",
                    {"spec": spec_name, "name": disease[:200]},
                ))
            for drug in ent_map.get("drugs", []):
                statements.append((
                    "MATCH (sp:Specialist {name: $spec}) "
                    "MATCH (dr:Drug {name: $name}) "
                    "MERGE (sp)-[:FOUND]->(dr)",
                    {"spec": spec_name, "name": drug[:200]},
                ))
            for gene in ent_map.get("genes", []):
                statements.append((
                    "MATCH (sp:Specialist {name: $spec}) "
                    "MATCH (g:Gene {name: $name}) "
                    "MERGE (sp)-[:FOUND]->(g)",
                    {"spec": spec_name, "name": gene[:100]},
                ))

    # 6. Entity -[:EVIDENCED_BY]-> Study edges
    if entity_studies:
        for entity_name, study_ids in entity_studies.items():
            for sid in study_ids:
                # Determine merge property from the study ID format
                if sid.upper().startswith("NCT"):
                    match_prop = "nct_id"
                    match_val = sid.upper()
                elif sid.startswith("10."):
                    match_prop = "doi"
                    match_val = sid
                else:
                    match_prop = "pmid"
                    match_val = sid

                # Try Disease, then Drug, then Gene — entity_name could be any
                # We use a union-style approach: try each label with OPTIONAL MATCH
                # Simpler: emit one statement per label. Only one will actually match.
                for label, alias in [("Disease", "d"), ("Drug", "dr"), ("Gene", "g")]:
                    statements.append((
                        f"MATCH (e:{label} {{name: $ename}}) "
                        f"MATCH (st:Study {{{match_prop}: $study_id}}) "
                        "MERGE (e)-[:EVIDENCED_BY]->(st)",
                        {"ename": entity_name[:200], "study_id": match_val},
                    ))

    # 7. Study -[:CITED]-> Study cross-reference edges
    if cross_references:
        for src_id, tgt_id in cross_references:
            # Determine merge property for source study
            if src_id.upper().startswith("NCT"):
                src_prop, src_val = "nct_id", src_id.upper()
            elif src_id.startswith("10."):
                src_prop, src_val = "doi", src_id
            else:
                src_prop, src_val = "pmid", src_id

            # Determine merge property for target study
            if tgt_id.upper().startswith("NCT"):
                tgt_prop, tgt_val = "nct_id", tgt_id.upper()
            elif tgt_id.startswith("10."):
                tgt_prop, tgt_val = "doi", tgt_id
            else:
                tgt_prop, tgt_val = "pmid", tgt_id

            statements.append((
                f"MATCH (s1:Study {{{src_prop}: $src_id}}) "
                f"MATCH (s2:Study {{{tgt_prop}: $tgt_id}}) "
                "WHERE s1 <> s2 "
                "MERGE (s1)-[:CITED]->(s2)",
                {"src_id": src_val, "tgt_id": tgt_val},
            ))

    return statements


# ── DAG attribution map builders ─────────────────────────────────────

# Regex to find all [[pmid:XXXX]] references in text
_INLINE_PMID_REF_RE = re.compile(r"\[\[pmid:(\d{6,9})\]\]")


def _build_specialist_entities(
    sources: list[dict],
    specialists_used: list[str],
    diseases: list[str],
    drugs: list[str],
    genes: list[str],
) -> dict[str, dict[str, list[str]]]:
    """Map specialist name → {diseases: [...], drugs: [...], genes: [...]}.

    Attribution strategy:
    - For each source with an ``agent_name``, extract entities from that
      source's title + snippet and attribute them to the named specialist.
    - Entities that cannot be attributed to any specific specialist are
      distributed across ALL specialists used in the session (fallback).
    """
    spec_ents: dict[str, dict[str, list[str]]] = {}

    # Initialise buckets for known specialists
    for spec in specialists_used:
        spec_ents[spec] = {"diseases": [], "drugs": [], "genes": []}

    attributed_diseases: set[str] = set()
    attributed_drugs: set[str] = set()
    attributed_genes: set[str] = set()

    diseases_lower = {d.lower(): d for d in diseases}
    drugs_lower = {d.lower(): d for d in drugs}
    # Genes are case-sensitive (uppercase)
    genes_set = set(genes)

    for src in sources:
        agent = (src.get("agent_name") or "").strip()
        if not agent:
            continue
        if agent not in spec_ents:
            spec_ents[agent] = {"diseases": [], "drugs": [], "genes": []}

        text_block = (
            (src.get("title") or "") + " " + (src.get("snippet") or "")
        )
        text_lower = text_block.lower()

        # Attribute diseases
        for d_lower, d_orig in diseases_lower.items():
            if d_lower in text_lower:
                if d_orig not in spec_ents[agent]["diseases"]:
                    spec_ents[agent]["diseases"].append(d_orig)
                attributed_diseases.add(d_orig)

        # Attribute drugs
        for dr_lower, dr_orig in drugs_lower.items():
            if dr_lower in text_lower:
                if dr_orig not in spec_ents[agent]["drugs"]:
                    spec_ents[agent]["drugs"].append(dr_orig)
                attributed_drugs.add(dr_orig)

        # Attribute genes (case-sensitive match in original text)
        for g in genes_set:
            if g in text_block:
                if g not in spec_ents[agent]["genes"]:
                    spec_ents[agent]["genes"].append(g)
                attributed_genes.add(g)

    # Fallback: distribute unattributed entities across all specialists
    unattr_diseases = [d for d in diseases if d not in attributed_diseases]
    unattr_drugs = [d for d in drugs if d not in attributed_drugs]
    unattr_genes = [g for g in genes if g not in attributed_genes]

    if unattr_diseases or unattr_drugs or unattr_genes:
        for spec in specialists_used:
            if spec not in spec_ents:
                spec_ents[spec] = {"diseases": [], "drugs": [], "genes": []}
            for d in unattr_diseases:
                if d not in spec_ents[spec]["diseases"]:
                    spec_ents[spec]["diseases"].append(d)
            for dr in unattr_drugs:
                if dr not in spec_ents[spec]["drugs"]:
                    spec_ents[spec]["drugs"].append(dr)
            for g in unattr_genes:
                if g not in spec_ents[spec]["genes"]:
                    spec_ents[spec]["genes"].append(g)

    # Prune empty specialist entries
    return {k: v for k, v in spec_ents.items() if v["diseases"] or v["drugs"] or v["genes"]}


def _build_entity_studies(
    sources: list[dict],
    diseases: list[str],
    drugs: list[str],
    genes: list[str],
) -> dict[str, list[str]]:
    """Map entity name → [study primary IDs] based on text co-occurrence.

    For each source with a primary ID (pmid/nct_id/doi), checks whether
    entity names appear in the source's title + snippet.
    """
    entity_studies: dict[str, list[str]] = {}

    for src in sources:
        primary_id = src.get("pmid") or src.get("nct_id") or src.get("doi") or ""
        if not primary_id:
            continue
        primary_id = str(primary_id)

        title_and_snippet = (
            (src.get("title") or "") + " " + (src.get("snippet") or "")
        )
        title_and_snippet_lower = title_and_snippet.lower()

        for d in diseases:
            if d.lower() in title_and_snippet_lower:
                entity_studies.setdefault(d, [])
                if primary_id not in entity_studies[d]:
                    entity_studies[d].append(primary_id)

        for dr in drugs:
            if dr.lower() in title_and_snippet_lower:
                entity_studies.setdefault(dr, [])
                if primary_id not in entity_studies[dr]:
                    entity_studies[dr].append(primary_id)

        # Genes: case-sensitive match in original text
        for g in genes:
            if g in title_and_snippet:
                entity_studies.setdefault(g, [])
                if primary_id not in entity_studies[g]:
                    entity_studies[g].append(primary_id)

    return entity_studies


def _build_cross_references(findings_text: str) -> list[tuple[str, str]]:
    """Extract PMID cross-references from findings text.

    Finds paragraphs (or lines) that mention multiple ``[[pmid:X]]`` inline
    references and creates CITED edges between each pair.  This captures
    the notion that two studies are co-cited in support of the same claim.
    """
    cross_refs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # Split by double-newline (paragraph) or single newline
    paragraphs = re.split(r"\n\n+", findings_text) if findings_text else []
    if not paragraphs or (len(paragraphs) == 1 and "\n" in paragraphs[0]):
        paragraphs = findings_text.split("\n") if findings_text else []

    for para in paragraphs:
        pmids = list(set(_INLINE_PMID_REF_RE.findall(para)))
        if len(pmids) < 2:
            continue
        # Create pairs (undirected — use sorted order for dedup)
        for i in range(len(pmids)):
            for j in range(i + 1, len(pmids)):
                pair = (min(pmids[i], pmids[j]), max(pmids[i], pmids[j]))
                if pair not in seen:
                    seen.add(pair)
                    cross_refs.append(pair)

    return cross_refs


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

            # Prefer raw sources (has pmid/nct_id/doi/title/year for entity extraction)
            raw_sources = await client.get(f"{pfx}:evidence:published_sources_raw")
            if raw_sources:
                try:
                    data["sources"] = json.loads(raw_sources)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Fallback 1: citation_map_json (has id/title/snippet/url only)
            if not data["sources"]:
                citation_map_raw = await client.get(f"{pfx}:evidence:citation_map_json")
                if citation_map_raw:
                    try:
                        data["sources"] = json.loads(citation_map_raw)
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Fallback 2: try the old key path
            if not data["sources"]:
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
            "Links: QUERIED (Session→Specialist), FOUND (Specialist→Entity), "
            "EVIDENCED_BY (Entity→Study), CITED (Study→Study). "
            "Falls back to ABOUT (Session→Entity) when attribution data is unavailable.\n\n"
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

    # Placeholder session IDs that the LLM may hallucinate
    _PLACEHOLDER_SESSION_IDS = frozenset({
        "", "12345", "123456", "test", "session_id", "current_session",
        "session", "my_session", "example", "placeholder",
    })

    async def _do_write(self, args: dict, tool_context: ToolContext) -> dict:
        """Core write logic."""
        session_id = (args.get("session_id") or "").strip()

        # Defensive override: if the LLM passed a placeholder, get the real
        # session ID from tool_context (ADK always has the real one).
        real_session_id = ""
        if hasattr(tool_context, "session") and tool_context.session:
            real_session_id = getattr(tool_context.session, "id", "") or ""

        if session_id.lower() in self._PLACEHOLDER_SESSION_IDS:
            if real_session_id:
                log.warning(
                    "graph_writer: overriding placeholder session_id=%r with real=%s",
                    session_id, real_session_id[:12],
                )
                session_id = real_session_id
            else:
                return {
                    "success": False,
                    "error": "session_id is required (LLM passed placeholder and no real session available)",
                    "error_category": "validation_error",
                    "is_retryable": False,
                    "nodes_created": 0,
                    "edges_created": 0,
                }

        # Even when the LLM passes something non-placeholder, prefer the real
        # session ID if available (the LLM value may be truncated or wrong)
        if real_session_id and session_id != real_session_id:
            log.info(
                "graph_writer: using real session_id=%s (LLM passed=%s)",
                real_session_id[:12], session_id[:12],
            )
            session_id = real_session_id

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

        # Supplement with metadata-based extraction (catches entities that
        # inline tags miss — which is most of them in practice)
        meta_entities = extract_entities_from_source_metadata(sources, query_text)
        for d in meta_entities.get("diseases", []):
            if d not in diseases:
                diseases.append(d)
        for dr in meta_entities.get("drugs", []):
            if dr not in drugs:
                drugs.append(dr)
        for g in meta_entities.get("genes", []):
            if g not in genes:
                genes.append(g)

        log.info(
            "graph_writer: session=%s entities: %d specialists, %d studies, "
            "%d diseases, %d drugs, %d genes (inline: %d/%d/%d, meta: %d/%d/%d)",
            session_id[:12],
            len(specialists_used), len(studies),
            len(diseases), len(drugs), len(genes),
            len(inline.get("diseases", [])), len(inline.get("drugs", [])), len(inline.get("genes", [])),
            len(meta_entities.get("diseases", [])), len(meta_entities.get("drugs", [])), len(meta_entities.get("genes", [])),
        )

        # ── Build DAG attribution maps ───────────────────────────────
        specialist_entities = _build_specialist_entities(
            sources, specialists_used, diseases, drugs, genes,
        )
        entity_studies = _build_entity_studies(sources, diseases, drugs, genes)
        cross_references = _build_cross_references(findings_text)

        log.info(
            "graph_writer: session=%s DAG maps: %d specialist_entity edges, "
            "%d entity_study edges, %d cross_refs",
            session_id[:12],
            sum(
                len(v.get("diseases", [])) + len(v.get("drugs", [])) + len(v.get("genes", []))
                for v in specialist_entities.values()
            ),
            sum(len(ids) for ids in entity_studies.values()),
            len(cross_references),
        )

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
            specialist_entities=specialist_entities if specialist_entities else None,
            entity_studies=entity_studies if entity_studies else None,
            cross_references=cross_references if cross_references else None,
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
