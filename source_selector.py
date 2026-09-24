"""Small contract for answering from one approved retrieval record."""

from __future__ import annotations

import json
import re

from prompt_policy import SYSTEM_PROMPT, compile_runtime_prompt


ASK = "ASK"


def normalize_answer(value: object) -> str:
    """Normalize inline whitespace while retaining meaningful line breaks."""

    answer = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    answer = "\n".join(
        re.sub(r"[^\S\n]+", " ", line).strip()
        for line in answer.split("\n")
    )
    return re.sub(r"\n{2,}", "\n", answer).strip()


def build_prompt(
    records: list[dict],
    current_page_id: str = "",
    previous_answer: str = "",
    current_date: str = "",
    conversation_history: list[dict] | None = None,
    current_time: str = "",
    team_prompt: str = "",
) -> str:
    """Build a grounded prompt with bounded, server-sanitized conversation context."""

    return (
        compile_runtime_prompt(team_prompt)
        + "\nCURRENT DATE:\n"
        + json.dumps(current_date or None)
        + "\nCURRENT TIME (America/New_York; an ended session is not upcoming):\n"
        + json.dumps(current_time or None)
        + "\nCURRENT PAGE ID:\n"
        + json.dumps(current_page_id or None)
        + "\nPREVIOUS GUIDE ANSWER:\n"
        + json.dumps(previous_answer or None, ensure_ascii=False)
        + "\nRECENT CONVERSATION:\n"
        + json.dumps(conversation_history or [], ensure_ascii=False, indent=2)
        + "\nCANDIDATE RECORDS:\n"
        + json.dumps(records, ensure_ascii=False, indent=2)
    )


def parse_response(raw: str, allowed_ids):
    """Return one allowed record and the model's complete answer.

    Accept plain model prose without a repair generation. A single candidate
    resolves its citation; with several candidates, do not invent a source ID.
    """

    allowed = {str(value) for value in allowed_ids}
    text = str(raw or "").strip()
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        answer = normalize_answer(text)
        if answer and "{" not in text and "}" not in text:
            return {"pick": next(iter(allowed)) if len(allowed) == 1 else ASK, "answer": answer}
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if (not isinstance(parsed, dict) or not {"pick", "answer"}.issubset(parsed)
            or set(parsed) - {"pick", "answer", "action_url"}):
        return None
    pick = str(parsed.get("pick") or "").strip()
    answer = normalize_answer(parsed.get("answer"))
    if not answer:
        return None
    if pick != ASK and pick not in allowed:
        return None
    result = {"pick": pick, "answer": answer}
    if isinstance(parsed.get("action_url"), str):
        result["action_url"] = parsed["action_url"].strip()
    return result


def parse_pick(raw: str, allowed_ids) -> str:
    """Return an allowed source ID; malformed or unsupported output abstains."""

    allowed = {str(value) for value in allowed_ids}
    text = str(raw or "").strip()
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return ASK
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return ASK
    if not isinstance(parsed, dict) or set(parsed) != {"pick"}:
        return ASK
    pick = str(parsed.get("pick") or "").strip()
    if pick == ASK:
        return ASK
    return pick if pick in allowed else ASK
