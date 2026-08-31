#!/usr/bin/env python3
"""Report shared evaluator-state consistency without exposing transcript content.

The report includes account display names and aggregate review activity, but it
never selects email addresses, passwords, invitation tokens, transcript text,
raw conversation identifiers, or database credentials.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation_store import EvaluationStore


def _digest(rows: list[dict], fields: tuple[str, ...]) -> str:
    material = [
        {field: row.get(field) for field in fields}
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def audit() -> dict:
    store = EvaluationStore()
    store.open()
    try:
        accounts = store.list_accounts()
        claimed_slots = [row["slot_key"] for row in accounts if row["claimed"]]
        views = {}
        for slot in claimed_slots:
            conversations = store.list_conversations(slot, limit=500)
            buckets = store.list_buckets(slot)
            views[slot] = {
                "conversation_count": len(conversations),
                "conversation_digest": _digest(
                    conversations,
                    (
                        "id", "last_turn_at", "turn_count", "complete_turn_count",
                        "failed_turn_count", "bucket_id", "evaluation_version",
                    ),
                ),
                "bucket_count": len(buckets),
                "bucket_digest": _digest(
                    buckets,
                    ("id", "standard_key", "label", "sort_position", "version"),
                ),
            }

        with store._pool.connection() as connection:
            with connection.cursor(row_factory=store._dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT a.actor_slot,
                           COALESCE(ac.display_name, a.actor_slot) AS display_name,
                           a.action, COUNT(*)::INTEGER AS action_count
                    FROM evaluation_audit_events a
                    LEFT JOIN evaluator_accounts ac ON ac.slot_key = a.actor_slot
                    WHERE a.action IN (
                        'conversation.move', 'conversation.note',
                        'conversation.annotation', 'prompt.proposal.create',
                        'prompt.proposal.update', 'prompt.proposal.comment'
                    )
                    GROUP BY a.actor_slot, ac.display_name, a.action
                    ORDER BY a.actor_slot, a.action
                    """
                )
                activity = [dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT COUNT(*) FILTER (WHERE bucket_id IS NOT NULL)::INTEGER
                             AS placements,
                           COUNT(*) FILTER (WHERE note IS NOT NULL)::INTEGER AS notes
                    FROM conversation_evaluations
                    """
                )
                evaluation_counts = dict(cursor.fetchone())
                cursor.execute(
                    "SELECT COUNT(*)::INTEGER AS annotations FROM conversation_annotations"
                )
                annotation_count = int(cursor.fetchone()["annotations"])

        editor_three = next(
            (row for row in accounts if row["slot_key"] == "editor-3"),
            {"claimed": False, "invitation_active": False, "disabled": False},
        )
        view_values = list(views.values())
        return {
            "claimed_accounts": len(claimed_slots),
            "account_status": [
                {
                    "slot_key": row["slot_key"],
                    "display_name": row.get("display_name"),
                    "claimed": bool(row["claimed"]),
                    "invitation_active": bool(row["invitation_active"]),
                }
                for row in accounts
            ],
            "shared_view": {
                "conversation_counts_match": len({
                    row["conversation_count"] for row in view_values
                }) <= 1,
                "conversation_order_and_placements_match": len({
                    row["conversation_digest"] for row in view_values
                }) <= 1,
                "bucket_views_match": len({
                    (row["bucket_count"], row["bucket_digest"])
                    for row in view_values
                }) <= 1,
                "views": views,
            },
            "stored_review_state": {
                **evaluation_counts,
                "annotations": annotation_count,
            },
            "review_activity": activity,
            "editor_three": {
                "claimed": bool(editor_three["claimed"]),
                "invitation_active": bool(editor_three["invitation_active"]),
                "disabled": bool(editor_three["disabled"]),
            },
            "privacy": {
                "transcript_text_selected": False,
                "emails_selected": False,
                "raw_conversation_ids_printed": False,
                "credentials_printed": False,
            },
        }
    finally:
        store.close()


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, sort_keys=True, default=str))
