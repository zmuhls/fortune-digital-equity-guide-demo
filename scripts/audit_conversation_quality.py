#!/usr/bin/env python3
"""Read-only, aggregate data-quality checks for conversation capture.

This script never selects message content. It is safe to run against staging or
production with ``DATABASE_URL`` set in the operator environment.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _dependencies():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise RuntimeError("Install requirements.txt before running this audit") from error
    return psycopg, dict_row


def _json_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def run_audit(database_url: str) -> dict:
    if not str(database_url or "").strip():
        raise RuntimeError("DATABASE_URL is required")
    psycopg, dict_row = _dependencies()
    queries = {
        "profile": """
            SELECT
                (SELECT COUNT(*) FROM conversations)::INTEGER AS conversations,
                (SELECT COUNT(*) FROM conversation_turns)::INTEGER AS turns,
                (SELECT COUNT(*) FROM conversation_messages)::INTEGER AS messages,
                (SELECT MIN(started_at) FROM conversations) AS first_conversation_at,
                (SELECT MAX(last_turn_at) FROM conversations) AS latest_turn_at,
                (SELECT COUNT(*) FROM conversations WHERE expires_at <= NOW())::INTEGER
                    AS expired_not_purged
        """,
        "grain_and_integrity": """
            SELECT
                (COUNT(*) - COUNT(DISTINCT t.id))::INTEGER AS duplicate_turn_ids,
                (COUNT(*) - COUNT(DISTINCT t.client_event_id))::INTEGER AS duplicate_event_ids,
                COUNT(*) FILTER (WHERE c.id IS NULL)::INTEGER AS orphan_turns,
                COUNT(*) FILTER (
                    WHERE t.status = 'complete' AND (
                        t.completed_at IS NULL OR t.latency_ms IS NULL
                        OR t.response_kind IS NULL OR t.retrieval_scope IS NULL
                    )
                )::INTEGER AS incomplete_completed_turns,
                COUNT(*) FILTER (
                    WHERE t.status = 'pending'
                      AND t.created_at < NOW() - INTERVAL '5 minutes'
                )::INTEGER AS stale_pending_turns,
                COUNT(*) FILTER (WHERE t.latency_ms < 0)::INTEGER AS negative_latency_turns,
                COUNT(*) FILTER (
                    WHERE t.created_at > NOW() + INTERVAL '5 minutes'
                )::INTEGER AS future_turns,
                COUNT(*) FILTER (
                    WHERE t.status = 'complete'
                      AND t.prompt_policy_version <> 'legacy'
                      AND (
                        t.chat_stage = 'unknown'
                        OR t.request_kind = 'unknown'
                        OR t.request_language = 'und'
                        OR t.response_language = 'und'
                      )
                )::INTEGER AS versioned_turns_missing_interaction_context
            FROM conversation_turns AS t
            LEFT JOIN conversations AS c ON c.id = t.conversation_id
        """,
        "privacy_and_review": """
            WITH message_counts AS (
                SELECT
                    t.id,
                    t.capture_mode,
                    t.privacy_state,
                    t.review_state,
                    c.client_surface,
                    COUNT(m.id)::INTEGER AS message_count,
                    COUNT(*) FILTER (
                        WHERE m.ordinal = 0 AND m.role = 'user'
                    )::INTEGER AS valid_user,
                    COUNT(*) FILTER (
                        WHERE m.ordinal = 1 AND m.role = 'assistant'
                    )::INTEGER AS valid_assistant,
                    COUNT(*) FILTER (WHERE BTRIM(m.content) = '')::INTEGER
                        AS empty_messages
                FROM conversation_turns AS t
                JOIN conversations AS c ON c.id = t.conversation_id
                LEFT JOIN conversation_messages AS m ON m.turn_id = t.id
                GROUP BY t.id, c.client_surface
            )
            SELECT
                COUNT(*) FILTER (
                    WHERE message_count NOT IN (0, 2)
                )::INTEGER AS invalid_message_count,
                COUNT(*) FILTER (
                    WHERE message_count = 2
                      AND (valid_user <> 1 OR valid_assistant <> 1)
                )::INTEGER AS invalid_role_order,
                COALESCE(SUM(empty_messages), 0)::INTEGER AS empty_messages,
                COUNT(*) FILTER (
                    WHERE message_count > 0 AND privacy_state <> 'clear'
                )::INTEGER AS nonclear_turns_with_text,
                COUNT(*) FILTER (
                    WHERE message_count > 0 AND capture_mode <> 'transcript'
                )::INTEGER AS metadata_turns_with_text,
                COUNT(*) FILTER (
                    WHERE review_state = 'ready'
                      AND client_surface NOT IN ('replica', 'wix')
                )::INTEGER AS nonhuman_ready_turns,
                COUNT(*) FILTER (
                    WHERE review_state = 'ready'
                      AND (privacy_state <> 'clear' OR message_count <> 2)
                )::INTEGER AS unsafe_ready_turns
            FROM message_counts
        """,
        "latency": """
            SELECT
                COUNT(latency_ms)::INTEGER AS samples,
                MIN(latency_ms)::INTEGER AS min_ms,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms))::INTEGER
                    AS median_ms,
                ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms))::INTEGER
                    AS p95_ms,
                MAX(latency_ms)::INTEGER AS max_ms
            FROM conversation_turns
            WHERE status = 'complete'
        """,
        "dimensions": """
            SELECT COALESCE(JSONB_AGG(ROW_TO_JSON(d) ORDER BY d.dimension, d.value), '[]')
            FROM (
                SELECT 'surface' AS dimension, client_surface AS value, COUNT(*)::INTEGER AS count
                FROM conversations GROUP BY client_surface
                UNION ALL
                SELECT 'request_kind', request_kind, COUNT(*)::INTEGER
                FROM conversation_turns GROUP BY request_kind
                UNION ALL
                SELECT 'chat_stage', chat_stage, COUNT(*)::INTEGER
                FROM conversation_turns GROUP BY chat_stage
                UNION ALL
                SELECT 'request_language', request_language, COUNT(*)::INTEGER
                FROM conversation_turns GROUP BY request_language
                UNION ALL
                SELECT 'response_language', response_language, COUNT(*)::INTEGER
                FROM conversation_turns GROUP BY response_language
                UNION ALL
                SELECT 'retrieval_scope', COALESCE(retrieval_scope, '<null>'), COUNT(*)::INTEGER
                FROM conversation_turns GROUP BY retrieval_scope
                UNION ALL
                SELECT 'response_kind', COALESCE(response_kind, '<null>'), COUNT(*)::INTEGER
                FROM conversation_turns GROUP BY response_kind
                UNION ALL
                SELECT 'prompt_policy_version', prompt_policy_version, COUNT(*)::INTEGER
                FROM conversation_turns GROUP BY prompt_policy_version
            ) AS d(dimension, value, count)
        """,
        "evaluation": """
            SELECT
                (SELECT COUNT(*) FROM evaluator_accounts)::INTEGER AS account_slots,
                (SELECT COUNT(*) FROM evaluator_accounts WHERE claimed_at IS NOT NULL)::INTEGER
                    AS claimed_accounts,
                (SELECT COUNT(*) FROM evaluation_bucket_sets)::INTEGER AS bucket_sets,
                (SELECT COUNT(*) FROM evaluation_buckets WHERE archived_at IS NULL)::INTEGER
                    AS active_buckets,
                (SELECT COUNT(*) FROM conversation_evaluations)::INTEGER AS placements,
                (SELECT COUNT(*) FROM conversation_annotations)::INTEGER AS annotations,
                (
                    SELECT COUNT(*) FROM (
                        SELECT c.id
                        FROM conversations AS c
                        JOIN conversation_turns AS t ON t.conversation_id = c.id
                        WHERE c.capture_mode = 'transcript'
                          AND c.client_surface IN ('replica', 'wix')
                          AND c.expires_at > NOW()
                          AND c.last_turn_at <= NOW() - INTERVAL '60 seconds'
                          AND t.status = 'complete'
                          AND t.privacy_state = 'clear'
                          AND t.review_state = 'ready'
                          AND (
                              SELECT COUNT(*)
                              FROM conversation_messages AS m
                              WHERE m.turn_id = t.id
                          ) = 2
                        GROUP BY c.id
                    ) AS eligible
                )::INTEGER AS eligible_conversations,
                (
                    SELECT COUNT(*) FROM conversation_evaluations AS e
                    LEFT JOIN evaluation_buckets AS b
                      ON b.id = e.bucket_id AND b.bucket_set_id = e.bucket_set_id
                    WHERE e.bucket_id IS NOT NULL AND b.id IS NULL
                )::INTEGER AS orphan_placements,
                (
                    SELECT COUNT(*) FROM conversation_annotations AS a
                    LEFT JOIN conversation_messages AS m
                      ON m.id = a.message_id AND m.conversation_id = a.conversation_id
                    WHERE m.id IS NULL
                )::INTEGER AS orphan_annotations
        """,
    }
    result = {}
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            for name, query in queries.items():
                cursor.execute(query)
                row = cursor.fetchone()
                result[name] = _json_value(
                    row[next(iter(row))] if len(row) == 1 else dict(row)
                )

    failures = []
    for section in ("grain_and_integrity", "privacy_and_review"):
        for check, value in result[section].items():
            if int(value or 0) != 0:
                failures.append({"check": check, "value": int(value), "severity": "high"})
    if int(result["profile"]["expired_not_purged"] or 0) != 0:
        failures.append({
            "check": "expired_not_purged",
            "value": int(result["profile"]["expired_not_purged"]),
            "severity": "high",
        })
    for check in ("orphan_placements", "orphan_annotations"):
        if int(result["evaluation"][check] or 0) != 0:
            failures.append({
                "check": check,
                "value": int(result["evaluation"][check]),
                "severity": "high",
            })
    result["quality_gate"] = {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "message_content_selected": False,
    }
    return result


def main() -> int:
    try:
        result = run_audit(os.environ.get("DATABASE_URL", ""))
    except Exception as error:
        print(json.dumps({"quality_gate": {"status": "blocked", "error": str(error)}}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["quality_gate"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
