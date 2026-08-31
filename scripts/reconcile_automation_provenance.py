#!/usr/bin/env python3
"""Mark conversations named by versioned evaluation artifacts as automated.

The script selects identifiers and provenance columns only. It never reads
question or answer text. Without ``--apply`` it is a read-only audit.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "evals" / "website-guide" / "results"


def conversation_ids(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "conversation_id":
                try:
                    found.add(str(uuid.UUID(str(item))))
                except (ValueError, TypeError, AttributeError):
                    pass
            found.update(conversation_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(conversation_ids(item))
    return found


def artifact_ids(directory: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        try:
            found.update(conversation_ids(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=pathlib.Path, default=DEFAULT_RESULTS)
    parser.add_argument("--source", default="versioned-eval-artifact")
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    ids = sorted(artifact_ids(options.results))
    if not ids:
        print(json.dumps({"artifact_ids": 0, "matched": 0, "updated": 0}))
        return 0

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)::INTEGER AS matched,
                       COUNT(*) FILTER (WHERE is_automated)::INTEGER AS already_marked,
                       COUNT(*) FILTER (
                           WHERE client_surface IN ('replica', 'wix')
                       )::INTEGER AS review_surface_matches
                FROM conversations
                WHERE id = ANY(%s::uuid[])
                """,
                (ids,),
            )
            summary = dict(cursor.fetchone())
            updated = 0
            if options.apply:
                cursor.execute(
                    """
                    UPDATE conversations
                    SET is_automated = TRUE,
                        automation_source = COALESCE(automation_source, %s)
                    WHERE id = ANY(%s::uuid[])
                      AND (
                        NOT is_automated
                        OR automation_source IS NULL
                      )
                    """,
                    (options.source, ids),
                )
                updated = cursor.rowcount
            connection.commit()
    print(json.dumps({
        "artifact_ids": len(ids),
        **summary,
        "updated": updated,
        "applied": options.apply,
        "message_content_selected": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
