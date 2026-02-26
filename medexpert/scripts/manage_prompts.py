#!/usr/bin/env python3
"""CLI tool for managing prompt evolution candidates.

Usage:
    python scripts/manage_prompts.py list              # List pending candidates
    python scripts/manage_prompts.py approve AGENT VER # Approve a candidate
    python scripts/manage_prompts.py reject AGENT VER  # Reject a candidate
    python scripts/manage_prompts.py show AGENT VER    # Show candidate details

Examples:
    python scripts/manage_prompts.py list
    python scripts/manage_prompts.py show DrugSpecialist 3
    python scripts/manage_prompts.py approve DrugSpecialist 3 --reviewer admin
    python scripts/manage_prompts.py reject DrugSpecialist 3 --reason "too aggressive"
"""

import argparse
import json
import os
import sys

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _db_path() -> str:
    return os.environ.get("COLD_DB_PATH", "data/medexpert-cold.db")


def cmd_list(args):
    from lifesci_tools.cold_store import get_connection, get_pending_candidates

    conn = get_connection(_db_path())
    try:
        candidates = get_pending_candidates(conn)
        if not candidates:
            print("No pending prompt candidates.")
            return

        print(f"\n{'Agent':<30} {'Ver':>4}  {'Created':<20}  {'Regression':>10}")
        print("-" * 75)
        for c in candidates:
            meta = c.get("metadata") or {}
            reg_score = (meta.get("regression") or {}).get("avg_score", "N/A")
            if isinstance(reg_score, float):
                reg_score = f"{reg_score:.3f}"
            print(
                f"{c['agent_name']:<30} {c['version']:>4}  "
                f"{c['created_at']:<20}  {reg_score:>10}"
            )
        print(f"\n{len(candidates)} candidate(s) awaiting approval.")
    finally:
        conn.close()


def cmd_show(args):
    from lifesci_tools.cold_store import get_connection, get_prompt_version

    conn = get_connection(_db_path())
    try:
        v = get_prompt_version(conn, args.agent, args.version)
        if not v:
            print(f"No version found for {args.agent} v{args.version}")
            return

        meta = v.get("metadata") or {}
        print(f"\nAgent:       {v['agent_name']}")
        print(f"Version:     {v['version']}")
        print(f"Active:      {v.get('is_active', False)}")
        print(f"Baseline:    {v.get('is_baseline', False)}")
        print(f"Created:     {v.get('created_at', 'N/A')}")
        print(f"Evolved from: v{meta.get('evolved_from', 'N/A')}")

        reg = meta.get("regression", {})
        if reg:
            print(f"Regression:  avg={reg.get('avg_score', 'N/A')}, "
                  f"min={reg.get('min_score', 'N/A')}, "
                  f"passed={reg.get('passed', 'N/A')}")

        feedback = meta.get("failure_feedback", "")
        if feedback:
            print(f"\nFailure feedback:\n{feedback}")

        print(f"\nInstruction preview ({len(v.get('instruction', ''))} chars):")
        print(v.get("instruction", "")[:500])
        if len(v.get("instruction", "")) > 500:
            print("... (truncated)")
    finally:
        conn.close()


def cmd_approve(args):
    from lifesci_tools.cold_store import approve_candidate, get_connection

    conn = get_connection(_db_path())
    try:
        ok = approve_candidate(conn, args.agent, args.version, reviewer=args.reviewer)
        if ok:
            print(f"Approved and promoted {args.agent} v{args.version}")
        else:
            print(f"No candidate found for {args.agent} v{args.version}")
            sys.exit(1)
    finally:
        conn.close()


def cmd_reject(args):
    from lifesci_tools.cold_store import get_connection, reject_candidate

    conn = get_connection(_db_path())
    try:
        ok = reject_candidate(
            conn, args.agent, args.version,
            reviewer=args.reviewer, reason=args.reason,
        )
        if ok:
            print(f"Rejected {args.agent} v{args.version}")
        else:
            print(f"No candidate found for {args.agent} v{args.version}")
            sys.exit(1)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Manage prompt evolution candidates")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List pending candidates")

    p_show = sub.add_parser("show", help="Show candidate details")
    p_show.add_argument("agent", help="Agent name (e.g. DrugSpecialist)")
    p_show.add_argument("version", type=int, help="Version number")

    p_approve = sub.add_parser("approve", help="Approve a candidate")
    p_approve.add_argument("agent", help="Agent name")
    p_approve.add_argument("version", type=int, help="Version number")
    p_approve.add_argument("--reviewer", default="", help="Reviewer name")

    p_reject = sub.add_parser("reject", help="Reject a candidate")
    p_reject.add_argument("agent", help="Agent name")
    p_reject.add_argument("version", type=int, help="Version number")
    p_reject.add_argument("--reviewer", default="", help="Reviewer name")
    p_reject.add_argument("--reason", default="", help="Rejection reason")

    args = parser.parse_args()
    cmds = {"list": cmd_list, "show": cmd_show, "approve": cmd_approve, "reject": cmd_reject}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
