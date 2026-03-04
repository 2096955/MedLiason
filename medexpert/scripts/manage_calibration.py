#!/usr/bin/env python3
"""CLI tool for managing pending calibration weight changes.

Usage:
    python scripts/manage_calibration.py list                 # List pending calibrations
    python scripts/manage_calibration.py show ID              # Show calibration details
    python scripts/manage_calibration.py apply ID             # Apply a pending calibration
    python scripts/manage_calibration.py reject ID            # Reject a pending calibration

Examples:
    python scripts/manage_calibration.py list
    python scripts/manage_calibration.py show 3
    python scripts/manage_calibration.py apply 3 --reviewer admin
    python scripts/manage_calibration.py reject 3 --reason "delta too aggressive"
"""

import argparse
import os
import sys

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _db_path() -> str:
    return os.environ.get("COLD_DB_PATH", "data/medexpert-cold.db")


def cmd_list(args):
    from lifesci_tools.cold_store import get_connection, get_pending_calibrations

    conn = get_connection(_db_path())
    try:
        pending = get_pending_calibrations(conn, status="pending")
        if not pending:
            print("No pending calibrations.")
            return

        print(
            f"\n{'ID':>4}  {'Agent':<28} {'Domain':<16} "
            f"{'Current':>8} {'Suggested':>10} {'Delta':>6} {'Action':<14} {'Created':<20}"
        )
        print("-" * 120)
        for c in pending:
            print(
                f"{c['id']:>4}  {c['agent_name']:<28} {c['domain']:<16} "
                f"{c['current_weight']:>8.3f} {c['suggested_weight']:>10.3f} "
                f"{c['delta']:>6.3f} {c['action']:<14} {c['created_at']:<20}"
            )
        print(f"\n{len(pending)} calibration(s) awaiting review.")
    finally:
        conn.close()


def cmd_show(args):
    from lifesci_tools.cold_store import get_connection

    conn = get_connection(_db_path())
    try:
        row = conn.execute(
            "SELECT * FROM pending_calibration WHERE id = ?", (args.id,)
        ).fetchone()
        if not row:
            entry = None
        else:
            entry = dict(row)

        if not entry:
            print(f"No calibration found with id {args.id}")
            return

        print(f"\nCalibration #{entry['id']}")
        print(f"  Agent:           {entry['agent_name']}")
        print(f"  Domain:          {entry['domain']}")
        print(f"  Current weight:  {entry['current_weight']:.3f}")
        print(f"  Suggested:       {entry['suggested_weight']:.3f}")
        print(f"  Delta:           {entry['delta']:.4f}")
        print(f"  Negative rate:   {entry['negative_rate']:.4f}")
        print(f"  Sample count:    {entry['sample_count']}")
        print(f"  Action:          {entry['action']}")
        print(f"  Status:          {entry['status']}")
        print(f"  Created:         {entry['created_at']}")
        if entry.get("reviewer"):
            print(f"  Reviewer:        {entry['reviewer']}")
        if entry.get("reviewed_at"):
            print(f"  Reviewed at:     {entry['reviewed_at']}")
        if entry.get("reason"):
            print(f"  Reason:          {entry['reason']}")
    finally:
        conn.close()


def cmd_apply(args):
    from lifesci_tools.cold_store import apply_pending_calibration, get_connection

    conn = get_connection(_db_path())
    try:
        ok = apply_pending_calibration(conn, args.id, reviewer=args.reviewer)
        if ok:
            print(f"Applied calibration #{args.id}")
        else:
            print(f"No pending calibration found with id {args.id}")
            sys.exit(1)
    finally:
        conn.close()


def cmd_reject(args):
    from lifesci_tools.cold_store import get_connection, reject_pending_calibration

    conn = get_connection(_db_path())
    try:
        ok = reject_pending_calibration(
            conn, args.id, reviewer=args.reviewer, reason=args.reason
        )
        if ok:
            print(f"Rejected calibration #{args.id}")
        else:
            print(f"No pending calibration found with id {args.id}")
            sys.exit(1)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Manage pending calibration weight changes"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List pending calibrations")

    p_show = sub.add_parser("show", help="Show calibration details")
    p_show.add_argument("id", type=int, help="Calibration ID")

    p_apply = sub.add_parser("apply", help="Apply a pending calibration")
    p_apply.add_argument("id", type=int, help="Calibration ID")
    p_apply.add_argument("--reviewer", default="", help="Reviewer name")

    p_reject = sub.add_parser("reject", help="Reject a pending calibration")
    p_reject.add_argument("id", type=int, help="Calibration ID")
    p_reject.add_argument("--reviewer", default="", help="Reviewer name")
    p_reject.add_argument("--reason", default="", help="Rejection reason")

    args = parser.parse_args()
    cmds = {
        "list": cmd_list,
        "show": cmd_show,
        "apply": cmd_apply,
        "reject": cmd_reject,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
