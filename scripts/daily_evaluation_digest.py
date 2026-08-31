#!/usr/bin/env python3
"""Produce a concise, privacy-safe daily evaluation activity digest.

The report includes evaluator-authored review text after basic redaction, but it
never selects or prints participant conversation messages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any


EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
LONG_NUMBER = re.compile(r"(?<!\w)\d[\d\s().+-]{4,}\d(?!\w)")


def safe_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    text = EMAIL.sub("[redacted-email]", text)
    text = LONG_NUMBER.sub("[redacted-number]", text)
    return text[:limit]


def conversation_ref(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]


def iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def rows(cursor, query: str, params: tuple[Any, ...]) -> list[dict]:
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def build_digest(database_url: str, hours: int) -> dict:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise SystemExit("Install the PostgreSQL requirements before running the digest.") from error

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            prompt_edits = rows(
                cursor,
                """
                SELECT revision.release_number, revision.edit_number,
                       revision.change_note, revision.actor_slot,
                       COALESCE(account.display_name,
                                INITCAP(REPLACE(revision.actor_slot, '-', ' '))) AS actor_name,
                       revision.recorded_at, LENGTH(revision.body) AS character_count
                FROM shared_prompt_draft_revisions revision
                LEFT JOIN evaluator_accounts account
                  ON account.slot_key = revision.actor_slot
                WHERE revision.recorded_at >= %s AND revision.recorded_at < %s
                ORDER BY revision.recorded_at
                """,
                (start, end),
            )
            proposal_revisions = rows(
                cursor,
                """
                SELECT proposal.title, revision.proposal_version,
                       revision.action, revision.actor_slot,
                       COALESCE(account.display_name,
                                INITCAP(REPLACE(revision.actor_slot, '-', ' '))) AS actor_name,
                       revision.recorded_at,
                       ARRAY(SELECT jsonb_object_keys(revision.module_values)) AS module_keys
                FROM prompt_proposal_revisions revision
                JOIN prompt_proposals proposal ON proposal.id = revision.proposal_id
                LEFT JOIN evaluator_accounts account
                  ON account.slot_key = revision.actor_slot
                WHERE revision.recorded_at >= %s AND revision.recorded_at < %s
                ORDER BY revision.recorded_at
                """,
                (start, end),
            )
            proposal_comments = rows(
                cursor,
                """
                SELECT proposal.title, comment.body, comment.actor_slot,
                       COALESCE(account.display_name,
                                INITCAP(REPLACE(comment.actor_slot, '-', ' '))) AS actor_name,
                       comment.created_at
                FROM prompt_proposal_comments comment
                JOIN prompt_proposals proposal ON proposal.id = comment.proposal_id
                LEFT JOIN evaluator_accounts account
                  ON account.slot_key = comment.actor_slot
                WHERE comment.created_at >= %s AND comment.created_at < %s
                ORDER BY comment.created_at
                """,
                (start, end),
            )
            note_revisions = rows(
                cursor,
                """
                SELECT revision.conversation_id, revision.note, revision.action,
                       revision.actor_slot,
                       COALESCE(account.display_name,
                                INITCAP(REPLACE(revision.actor_slot, '-', ' '))) AS actor_name,
                       revision.recorded_at
                FROM conversation_note_revisions revision
                LEFT JOIN evaluator_accounts account
                  ON account.slot_key = revision.actor_slot
                WHERE revision.recorded_at >= %s AND revision.recorded_at < %s
                ORDER BY revision.recorded_at
                """,
                (start, end),
            )
            annotation_revisions = rows(
                cursor,
                """
                SELECT revision.conversation_id, revision.category, revision.note,
                       revision.action, revision.actor_slot,
                       COALESCE(account.display_name,
                                INITCAP(REPLACE(revision.actor_slot, '-', ' '))) AS actor_name,
                       revision.recorded_at
                FROM conversation_annotation_revisions revision
                LEFT JOIN evaluator_accounts account
                  ON account.slot_key = revision.actor_slot
                WHERE revision.recorded_at >= %s AND revision.recorded_at < %s
                ORDER BY revision.recorded_at
                """,
                (start, end),
            )
            attributions = rows(
                cursor,
                """
                SELECT revision.conversation_id, revision.evaluator_slot,
                       COALESCE(evaluator.display_name,
                                INITCAP(REPLACE(revision.evaluator_slot, '-', ' '))) AS evaluator_name,
                       revision.source, revision.actor_slot,
                       COALESCE(actor.display_name,
                                INITCAP(REPLACE(revision.actor_slot, '-', ' '))) AS actor_name,
                       revision.recorded_at
                FROM conversation_attribution_revisions revision
                LEFT JOIN evaluator_accounts evaluator
                  ON evaluator.slot_key = revision.evaluator_slot
                LEFT JOIN evaluator_accounts actor
                  ON actor.slot_key = revision.actor_slot
                WHERE revision.recorded_at >= %s AND revision.recorded_at < %s
                ORDER BY revision.recorded_at
                """,
                (start, end),
            )
            cursor.execute(
                """
                WITH active AS (
                    SELECT DISTINCT conversation_id
                    FROM conversation_turns
                    WHERE created_at >= %s AND created_at < %s
                ), totals AS (
                    SELECT t.conversation_id,
                           COUNT(*) AS all_turns,
                           COUNT(*) FILTER (WHERE t.status = 'failed') AS failed_turns,
                           COUNT(*) FILTER (
                               WHERE t.status = 'complete'
                                 AND NOT t.model_called
                                 AND t.privacy_state = 'clear'
                                 AND COALESCE(t.request_kind, 'retrieval')
                                     NOT IN ('privacy', 'sensitive')
                           ) AS complete_without_model
                    FROM conversation_turns t
                    JOIN active ON active.conversation_id = t.conversation_id
                    GROUP BY t.conversation_id
                )
                SELECT
                    COUNT(*) FILTER (
                        WHERE NOT c.is_automated
                          AND c.client_surface IN ('replica', 'wix')
                    )::INTEGER AS human_conversations,
                    COUNT(*) FILTER (WHERE c.is_automated)::INTEGER AS automated_conversations,
                    COUNT(*) FILTER (
                        WHERE NOT c.is_automated
                          AND c.client_surface IN ('replica', 'wix')
                          AND totals.all_turns > 3
                    )::INTEGER AS human_conversations_over_three_turns,
                    COALESCE(SUM(totals.failed_turns) FILTER (
                        WHERE NOT c.is_automated
                          AND c.client_surface IN ('replica', 'wix')
                    ), 0)::INTEGER AS failed_human_turns,
                    COALESCE(SUM(totals.complete_without_model) FILTER (
                        WHERE NOT c.is_automated
                          AND c.client_surface IN ('replica', 'wix')
                    ), 0)::INTEGER AS complete_turns_without_model
                FROM active
                JOIN conversations c ON c.id = active.conversation_id
                JOIN totals ON totals.conversation_id = active.conversation_id
                """,
                (start, end),
            )
            transcript_summary = dict(cursor.fetchone())
            cursor.execute(
                """
                SELECT prompt_policy_version, COUNT(*)::INTEGER AS turn_count
                FROM conversation_turns t
                JOIN conversations c ON c.id = t.conversation_id
                WHERE t.created_at >= %s AND t.created_at < %s
                  AND NOT c.is_automated
                  AND c.client_surface IN ('replica', 'wix')
                GROUP BY prompt_policy_version
                ORDER BY turn_count DESC, prompt_policy_version
                """,
                (start, end),
            )
            transcript_summary["prompt_policy_turns"] = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT response_kind, COUNT(*)::INTEGER AS turn_count
                FROM conversation_turns t
                JOIN conversations c ON c.id = t.conversation_id
                WHERE t.created_at >= %s AND t.created_at < %s
                  AND NOT c.is_automated
                  AND c.client_surface IN ('replica', 'wix')
                GROUP BY response_kind
                ORDER BY turn_count DESC, response_kind
                """,
                (start, end),
            )
            transcript_summary["human_response_kinds"] = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                FROM conversation_turns t
                JOIN conversations c ON c.id = t.conversation_id
                WHERE t.created_at >= %s AND t.created_at < %s
                  AND t.latency_ms IS NOT NULL
                  AND NOT c.is_automated
                  AND c.client_surface IN ('replica', 'wix')
                """,
                (start, end),
            )
            latency = cursor.fetchone()
            transcript_summary["latency_p95_ms"] = (
                round(float(latency["percentile_cont"]))
                if latency and latency["percentile_cont"] is not None
                else None
            )

    for item in prompt_edits:
        item["change_note"] = safe_text(item["change_note"])
        item["recorded_at"] = iso(item["recorded_at"])
        item["display_edit"] = f"v{item['release_number']}.{item['edit_number']}"
    for item in proposal_revisions:
        item["title"] = safe_text(item["title"], 80)
        item["recorded_at"] = iso(item["recorded_at"])
    for item in proposal_comments:
        item["title"] = safe_text(item["title"], 80)
        item["comment"] = safe_text(item.pop("body"), 500)
        item["created_at"] = iso(item["created_at"])
    for item in note_revisions:
        item["conversation"] = conversation_ref(item.pop("conversation_id"))
        item["note"] = safe_text(item["note"], 1000)
        item["recorded_at"] = iso(item["recorded_at"])
    for item in annotation_revisions:
        item["conversation"] = conversation_ref(item.pop("conversation_id"))
        item["note"] = safe_text(item["note"], 500)
        item["recorded_at"] = iso(item["recorded_at"])
    for item in attributions:
        item["conversation"] = conversation_ref(item.pop("conversation_id"))
        item["recorded_at"] = iso(item["recorded_at"])

    questions: list[str] = []
    if prompt_edits or proposal_revisions or proposal_comments:
        questions.append("Which prompt edits should move into regression testing before activation?")
    if note_revisions or annotation_revisions:
        questions.append("Which recurring evaluator finding should be implemented first?")
    if transcript_summary["human_conversations_over_three_turns"] or transcript_summary["failed_human_turns"]:
        questions.append("Should the new long or failed conversations become named regression cases?")

    return {
        "window": {"start": iso(start), "end": iso(end), "hours": hours},
        "prompt_activity": {
            "shared_draft_edits": prompt_edits,
            "proposal_revisions": proposal_revisions,
            "proposal_comments": proposal_comments,
        },
        "review_feedback": {
            "note_revisions": note_revisions,
            "annotation_revisions": annotation_revisions,
            "attribution_changes": attributions,
        },
        "transcripts": transcript_summary,
        "decision_questions": questions[:3],
        "privacy": "No participant message text, credentials, account emails, tokens, or database URLs are included.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    hours = min(max(args.hours, 1), 168)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required.")
    print(json.dumps(build_digest(database_url, hours), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
