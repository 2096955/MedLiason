"""Strategy Seeder — reads learned strategies from cold SQLite and formats
them for hot-store seeding at the start of a new session.

Returns ``{"seeded": False}`` gracefully when the cold store is empty,
unavailable, or the db_path is not configured.
"""

import logging
from typing import Any

from lifesci_tools import cold_store

log = logging.getLogger(__name__)


def build_seed_payload(query: str, db_path: str) -> dict[str, Any]:
    """Build a seed payload from cold store intelligence.

    Parameters
    ----------
    query : str
        The user's incoming question (used for normalisation and domain lookup).
    db_path : str
        Path to the cold-store SQLite database.

    Returns
    -------
    dict with keys: seeded, routing_hints, source_rankings, query_intelligence,
    agent_pairs. ``seeded`` is False when no useful data was found.
    """
    if not db_path:
        return {"seeded": False, "reason": "cold_db_path not configured"}

    try:
        conn = cold_store.get_connection(db_path)
    except Exception as exc:
        log.warning("Cannot open cold store at %s: %s", db_path, exc)
        return {"seeded": False, "reason": f"cold store unavailable: {exc}"}

    try:
        normalized = cold_store.normalize_query(query) if query else ""

        # Query intelligence (exact normalised match)
        query_intel = cold_store.get_query_intelligence(conn, normalized) if normalized else None

        # Determine domain from query intelligence or fall back to empty
        domain = (query_intel or {}).get("domain", "")

        # Routing hints for this domain
        routing_hints = cold_store.get_routing_strategy(conn, domain) if domain else None

        # Global source rankings
        source_rankings = cold_store.get_source_rankings(conn)

        # Global best agent pairs
        agent_pairs = cold_store.get_best_agent_pairs(conn)

        has_data = any([routing_hints, source_rankings, query_intel, agent_pairs])

        return {
            "seeded": has_data,
            "routing_hints": routing_hints,
            "source_rankings": source_rankings,
            "query_intelligence": query_intel,
            "agent_pairs": agent_pairs,
        }
    except Exception as exc:
        log.exception("Error building seed payload")
        return {"seeded": False, "reason": f"seed error: {exc}"}
    finally:
        conn.close()
