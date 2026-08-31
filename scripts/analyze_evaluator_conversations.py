#!/usr/bin/env python3
"""Privacy-safe structural review of human evaluator conversations.

The script reads message text only in memory to derive categorical quality
signals. It never prints message text, raw conversation or turn identifiers,
email addresses, account data, or database credentials.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from collections import OrderedDict
from typing import Any


VISIBLE_TURN_PREDICATE = """
(
  (
    t.status = 'complete'
    AND t.privacy_state = 'clear'
    AND t.review_state = 'ready'
    AND (
      SELECT COUNT(*) FROM conversation_messages review_messages
      WHERE review_messages.turn_id = t.id
    ) = 2
  )
  OR
  (
    t.status = 'failed'
    AND t.privacy_state = 'clear'
    AND t.review_state = 'excluded'
    AND (
      (
        SELECT COUNT(*) FROM conversation_messages failed_messages
        WHERE failed_messages.turn_id = t.id
      ) = 0
      OR (
        (
          SELECT COUNT(*) FROM conversation_messages failed_messages
          WHERE failed_messages.turn_id = t.id
        ) = 1
        AND EXISTS (
          SELECT 1 FROM conversation_messages failed_user
          WHERE failed_user.turn_id = t.id
            AND failed_user.ordinal = 0
            AND failed_user.role = 'user'
        )
      )
    )
  )
)
""".strip()


TOPIC_PATTERNS = OrderedDict(
    (
        ("excel", r"\b(excel|spreadsheet|worksheet|formula|formatting|organizing data|presenting data)\b"),
        ("email", r"\b(email|inbox|attachment|reply|forward)\b"),
        (
            "calendar",
            r"\b(calendar|schedule|when|what time|date|today|tomorrow|"
            r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        ),
        ("device", r"\b(device|laptop|phone|computer to keep|distribution)\b"),
        ("registration", r"\b(register|registration|sign up|enroll|join|contact)\b"),
        (
            "support",
            r"\b(support|help using|tutor|office hours|walk in|appointment|tech time|lab)\b",
        ),
        ("classes", r"\b(class|classes|course|workshop|training|learn)\b"),
        ("identity", r"\b(who are you|what are you|how do you work)\b"),
    )
)


def _dependencies():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise RuntimeError("Install requirements.txt before running this analysis") from error
    return psycopg, dict_row


def _fold(value: Any) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())


def _topic(value: Any) -> str:
    folded = _fold(value)
    for name, pattern in TOPIC_PATTERNS.items():
        if re.search(pattern, folded):
            return name
    return "other"


def _source_labels(response: Any) -> list[str]:
    if not isinstance(response, dict):
        return []
    labels = []
    for item in response.get("sources") or []:
        if not isinstance(item, dict):
            continue
        label = item.get("id") or item.get("url") or item.get("title")
        if label:
            labels.append(str(label))
    return labels


def _source_topic(labels: list[str]) -> str:
    joined = " ".join(labels).lower()
    patterns = (
        ("excel", r"excel"),
        ("email", r"email"),
        ("calendar", r"calendar"),
        ("device", r"device"),
        ("registration", r"contact|register"),
        ("support", r"support|individual"),
        ("classes", r"training|workshop|catalog"),
    )
    for name, pattern in patterns:
        if re.search(pattern, joined):
            return name
    return "none" if not labels else "other"


def _json_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def analyze(database_url: str) -> dict:
    if not str(database_url or "").strip():
        raise RuntimeError("DATABASE_URL is required")
    psycopg, dict_row = _dependencies()
    query = f"""
        SELECT c.id AS conversation_id, c.started_at, c.last_turn_at,
               t.id AS turn_id, t.sequence, t.status, t.error_code,
               t.prompt_policy_version, t.response_kind, t.retrieval_scope,
               t.model_called, t.response_json, t.page_context,
               m.ordinal, m.role, m.content
        FROM conversations c
        JOIN conversation_turns t ON t.conversation_id = c.id
        LEFT JOIN conversation_messages m ON m.turn_id = t.id
        WHERE c.capture_mode = 'transcript'
          AND c.client_surface IN ('replica', 'wix')
          AND c.expires_at > NOW()
          AND c.last_turn_at <= NOW() - INTERVAL '60 seconds'
          AND {VISIBLE_TURN_PREDICATE}
        ORDER BY c.last_turn_at DESC, c.id, t.sequence, m.ordinal
    """
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(query).fetchall()

    conversations: OrderedDict[str, dict] = OrderedDict()
    for row in rows:
        conversation = conversations.setdefault(
            str(row["conversation_id"]),
            {
                "started_at": row["started_at"],
                "last_turn_at": row["last_turn_at"],
                "turns": OrderedDict(),
            },
        )
        turn = conversation["turns"].setdefault(
            str(row["turn_id"]),
            {
                "sequence": int(row["sequence"]),
                "status": row["status"],
                "error_code": row["error_code"],
                "prompt": row["prompt_policy_version"],
                "kind": row["response_kind"],
                "scope": row["retrieval_scope"],
                "model_called": bool(row["model_called"]),
                "response": row["response_json"]
                if isinstance(row["response_json"], dict)
                else {},
                "page": row["page_context"]
                if isinstance(row["page_context"], dict)
                else {},
                "user": "",
                "assistant": "",
            },
        )
        if row["role"] in {"user", "assistant"}:
            turn[row["role"]] = str(row["content"] or "")

    records = []
    for index, conversation in enumerate(conversations.values(), 1):
        turns = sorted(conversation["turns"].values(), key=lambda item: item["sequence"])
        previous_answer = ""
        previous_source = ""
        recent_topic = "other"
        repeat_flags = 0
        context_loss_flags = 0
        source_switches = 0
        details = []
        for turn in turns:
            user_topic = _topic(turn["user"])
            if user_topic != "other":
                recent_topic = user_topic
            sources = _source_labels(turn["response"])
            source_topic = _source_topic(sources)
            similarity = (
                round(
                    difflib.SequenceMatcher(
                        None, _fold(previous_answer), _fold(turn["assistant"])
                    ).ratio(),
                    2,
                )
                if previous_answer and turn["assistant"]
                else 0
            )
            repeated = similarity >= 0.72 and len(_fold(turn["assistant"]).split()) >= 8
            repeat_flags += int(repeated)
            folded_question = _fold(turn["user"])
            elliptical = bool(
                len(folded_question.split()) <= 8
                or re.search(
                    r"\b(it|that|this|they|there|what about|and what|how about)\b",
                    folded_question,
                )
            )
            context_loss = bool(
                elliptical
                and recent_topic != "other"
                and source_topic not in {"none", "other", recent_topic}
                and turn["status"] == "complete"
            )
            context_loss_flags += int(context_loss)
            source_key = "|".join(sources)
            if previous_source and source_key and source_key != previous_source:
                source_switches += 1
            if source_key:
                previous_source = source_key
            details.append(
                {
                    "turn": turn["sequence"],
                    "status": turn["status"],
                    "error": turn["error_code"],
                    "prompt": turn["prompt"],
                    "kind": turn["kind"],
                    "scope": turn["scope"],
                    "model_called": turn["model_called"],
                    "user_topic": user_topic,
                    "carried_topic": recent_topic,
                    "elliptical": elliptical,
                    "source_topic": source_topic,
                    "source_count": len(sources),
                    "user_words": len(folded_question.split()),
                    "answer_words": len(_fold(turn["assistant"]).split()),
                    "repeat_similarity": similarity,
                    "repeat_flag": repeated,
                    "context_loss_flag": context_loss,
                }
            )
            if turn["assistant"]:
                previous_answer = turn["assistant"]
        page_titles = [
            str(turn["page"].get("title") or "Unknown page") for turn in turns
        ]
        records.append(
            {
                "label": f"C{index:02d}",
                "started_at": conversation["started_at"],
                "last_turn_at": conversation["last_turn_at"],
                "page_title": next(
                    (title for title in reversed(page_titles) if title != "Unknown page"),
                    "Unknown page",
                ),
                "turn_count": len(turns),
                "complete_turns": sum(turn["status"] == "complete" for turn in turns),
                "failed_turns": sum(turn["status"] == "failed" for turn in turns),
                "repeat_flags": repeat_flags,
                "context_loss_flags": context_loss_flags,
                "source_switches": source_switches,
                "turns": details,
            }
        )

    return _json_value(
        {
            "conversation_count": len(records),
            "long_conversation_count": sum(item["turn_count"] > 3 for item in records),
            "failed_only_count": sum(
                item["complete_turns"] == 0 and item["failed_turns"] > 0
                for item in records
            ),
            "records": records,
            "privacy": {
                "message_text_printed": False,
                "raw_identifiers_printed": False,
                "account_data_selected": False,
            },
        }
    )


def main() -> int:
    try:
        result = analyze(os.environ.get("DATABASE_URL", ""))
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": str(error)}))
        return 2
    mode = str(os.environ.get("FORTUNE_ANALYSIS_MODE", "all")).strip().lower()
    if mode == "long":
        result["records"] = [
            record for record in result["records"] if record["turn_count"] > 3
        ]
    elif mode == "summary":
        result["records"] = [
            {key: value for key, value in record.items() if key != "turns"}
            for record in result["records"]
        ]
    elif mode != "all":
        print(json.dumps({"status": "blocked", "error": "Unknown analysis mode"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
