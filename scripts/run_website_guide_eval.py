#!/usr/bin/env python3
"""Run the fixed synthetic Website Guide benchmark against an HTTP deployment."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import pathlib
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evals" / "website-guide" / "cases.json"
DEFAULT_SPEC = ROOT / "evals" / "website-guide" / "spec.json"
ALLOWED_SOURCE_HOSTS = {
    "fortunedigitalequity.org",
    "www.fortunedigitalequity.org",
}
LEVELS = {"hard", "release", "diagnostic"}
RESPONSE_KINDS = {"answer", "clarify", "handoff", "privacy"}
REQUEST_KINDS = {"privacy", "retrieval", "sensitive"}
EXPECTATION_OVERRIDE_FIELDS = {
    "advancement_required",
    "max_message_words",
    "message_contains_any",
    "message_excludes",
    "source_excludes",
    "source_match_any",
}
REQUIRED_FIELDS = {
    "kind",
    "message",
    "reason",
    "sources",
    "related",
    "choices",
    "handoff_url",
    "model",
    "model_called",
    "retrieval_scope",
    "continuation",
    "conversation_id",
    "turn_id",
    "client_event_id",
    "message_ids",
    "capture",
    "chat_stage",
    "request_kind",
    "request_language",
    "response_language",
    "prompt_policy_version",
}


def load_json(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def file_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_grader_overrides(
    document: dict,
    spec: dict,
    *,
    unit_kind: str,
) -> dict:
    """Apply a bounded, versioned expectation overlay without changing cases."""

    if unit_kind not in {"cases", "turns"}:
        raise ValueError("unit_kind must be cases or turns")
    grader_overrides = spec.get("grader_overrides", {})
    if not grader_overrides:
        return document
    if not isinstance(grader_overrides, dict):
        raise ValueError("grader_overrides must be an object")
    expected_key = "case_expectations" if unit_kind == "cases" else "turn_expectations"
    unexpected = set(grader_overrides).difference(
        {expected_key, "clarification_authority"}
    )
    if unexpected:
        raise ValueError(
            "unsupported grader override groups: " + ", ".join(sorted(unexpected))
        )
    overrides = grader_overrides.get(expected_key, {})
    if not isinstance(overrides, dict):
        raise ValueError(f"{expected_key} must be an object")
    clarification_authority = grader_overrides.get("clarification_authority")
    if clarification_authority not in {None, "sources_or_choice_targets"}:
        raise ValueError(
            "clarification_authority must be sources_or_choice_targets when set"
        )

    result = copy.deepcopy(document)
    if unit_kind == "cases":
        units = {case.get("id"): case for case in result.get("cases", [])}
    else:
        units = {
            f"{episode.get('id')}/{turn.get('id')}": turn
            for episode in result.get("episodes", [])
            for turn in episode.get("turns", [])
        }
    if clarification_authority == "sources_or_choice_targets":
        for unit in units.values():
            unit.setdefault("expect", {})[
                "clarify_authority_from_choices"
            ] = True
    for unit_id, expectation_override in overrides.items():
        if unit_id not in units:
            raise ValueError(f"grader override references unknown {unit_kind[:-1]} {unit_id!r}")
        if not isinstance(expectation_override, dict):
            raise ValueError(f"grader override for {unit_id!r} must be an object")
        unsupported = set(expectation_override).difference(EXPECTATION_OVERRIDE_FIELDS)
        if unsupported:
            raise ValueError(
                f"grader override for {unit_id!r} has unsupported fields: "
                + ", ".join(sorted(unsupported))
            )
        if "advancement_required" in expectation_override:
            if unit_kind != "turns" or not isinstance(
                expectation_override["advancement_required"], bool
            ):
                raise ValueError(
                    "advancement_required is a boolean multi-turn expectation"
                )
        if "max_message_words" in expectation_override:
            limit = expectation_override["max_message_words"]
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 48:
                raise ValueError("max_message_words must be an integer from 1 to 48")
        for field in (
            "message_contains_any",
            "message_excludes",
            "source_excludes",
            "source_match_any",
        ):
            if field not in expectation_override:
                continue
            values = expectation_override[field]
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                raise ValueError(f"{field} must be a list of non-empty strings")
        units[unit_id].setdefault("expect", {}).update(expectation_override)
    return result


def git_value(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def expanded_message(case: dict) -> str:
    if "message" in case:
        return str(case["message"])
    repeated = case.get("message_repeat")
    if isinstance(repeated, dict):
        value = str(repeated.get("value", ""))
        count = repeated.get("count")
        if isinstance(count, int) and 0 <= count <= 10_000:
            return value * count
    raise ValueError(f"case {case.get('id', '<missing>')} needs message or message_repeat")


def validate_suite(document: dict) -> list[str]:
    errors = []
    cases = document.get("cases")
    if not isinstance(cases, list):
        return ["cases must be a list"]
    if len(cases) < 25:
        errors.append(f"suite must contain at least 25 cases; got {len(cases)}")
    seen = set()
    slices = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{prefix}.id must be a non-empty string")
        elif case_id in seen:
            errors.append(f"duplicate case id: {case_id}")
        else:
            seen.add(case_id)
        if case.get("level") not in LEVELS:
            errors.append(f"{prefix}.level must be one of {sorted(LEVELS)}")
        if not isinstance(case.get("slice"), str) or not case["slice"]:
            errors.append(f"{prefix}.slice must be a non-empty string")
        else:
            slices.add(case["slice"])
        if not isinstance(case.get("expect"), dict):
            errors.append(f"{prefix}.expect must be an object")
        try:
            expanded_message(case)
        except ValueError as error:
            errors.append(str(error))
    if len(slices) < 8:
        errors.append(f"suite must cover at least 8 slices; got {len(slices)}")
    if not any(case.get("level") == "hard" for case in cases if isinstance(case, dict)):
        errors.append("suite needs at least one hard-gate case")
    expected_response_kinds = {
        value
        for case in cases if isinstance(case, dict)
        for value in case.get("expect", {}).get("kind_in", [])
    }
    expected_request_kinds = {
        value
        for case in cases if isinstance(case, dict)
        for value in case.get("expect", {}).get("request_kind_in", [])
    }
    if expected_response_kinds != RESPONSE_KINDS:
        errors.append(
            "suite must explicitly cover every response kind; got "
            + ", ".join(sorted(expected_response_kinds))
        )
    if expected_request_kinds != REQUEST_KINDS:
        errors.append(
            "suite must explicitly cover every request kind; got "
            + ", ".join(sorted(expected_request_kinds))
        )
    return errors


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 30,
) -> tuple[int, dict, float, str | None]:
    body = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "fortune-website-guide-synthetic-eval/1.0",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Origin"] = "https://zmuhls.github.io"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
        value = json.loads(raw) if raw else {}
        if not isinstance(value, dict):
            value = {"_non_object_response": value}
        return status, value, (time.perf_counter() - started) * 1000, None
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            value = {"_raw": raw[:2000]}
        if not isinstance(value, dict):
            value = {"_non_object_response": value}
        return error.code, value, (time.perf_counter() - started) * 1000, None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, {}, (time.perf_counter() - started) * 1000, type(error).__name__


def valid_uuid(value: object) -> bool:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return str(parsed) == str(value)


def source_blob(response: dict) -> str:
    values = []
    for source in response.get("sources", []):
        if not isinstance(source, dict):
            continue
        values.extend(str(source.get(key, "")) for key in ("id", "title", "url"))
    return " ".join(values).casefold()


def choice_blob(response: dict) -> str:
    values = []
    for choice in response.get("choices", []):
        if not isinstance(choice, dict):
            continue
        values.extend(str(choice.get(key, "")) for key in ("id", "label", "prompt", "url"))
    return " ".join(values).casefold()


def artifact_response(response: dict) -> dict:
    """Preserve response evidence without publishing continuation credentials."""

    result = copy.deepcopy(response)
    if result.get("conversation_token"):
        result["conversation_token"] = "[redacted]"
    return result


def blob_contains(blob: str, term: str) -> bool:
    raw_blob = str(blob or "").casefold()
    raw_term = str(term or "").casefold()
    if raw_term in raw_blob:
        return True
    normalize = lambda value: " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))
    normalized_term = normalize(raw_term)
    return bool(normalized_term) and normalized_term in normalize(raw_blob)


_NEGATED_EVIDENCE_FRAME = re.compile(
    r"(?:"
    r"\b(?:do|does|did|can|could|will|would|should|has|have|had)\s+not\s+"
    r"(?:[^\W\d_]+ly\s+){0,2}"
    r"(?:say|state|show|indicate|confirm|verify|establish|support|mention|"
    r"promise|guarantee|specify|list)\b"
    r"|\b(?:don't|doesn't|didn't|can't|cannot|couldn't|won't|wouldn't|"
    r"shouldn't|hasn't|haven't|hadn't)\s+"
    r"(?:[^\W\d_]+ly\s+){0,2}"
    r"(?:say|state|show|indicate|confirm|verify|establish|support|mention|"
    r"promise|guarantee|specify|list)\b"
    r"|\b(?:is|are|was|were)\s+not\s+"
    r"(?:clear|confirmed|verified|stated|shown|indicated|specified|listed)\b"
    r"|\b(?:isn't|aren't|wasn't|weren't)\s+"
    r"(?:clear|confirmed|verified|stated|shown|indicated|specified|listed)\b"
    r"|\bno\s+(?:clear\s+)?"
    r"(?:evidence|confirmation|indication|statement)\b"
    r")"
)
_NEGATION_BREAK = re.compile(r"\b(?:but|however|yet|nevertheless)\b")
_POSITIVE_EVIDENCE_FRAME = re.compile(
    r"\b(?:says?|states?|shows?|indicates?|confirms?|verifies?|establishes?|"
    r"supports?|mentions?|promises?|guarantees?|specifies?|lists?)\b"
)


def message_has_unnegated_excluded_text(message: str, term: str) -> bool:
    """Return true when an excluded phrase occurs outside an evidence denial."""

    folded_message = str(message or "").casefold()
    folded_term = str(term or "").casefold()
    if not folded_term:
        return False
    occurrences = list(re.finditer(re.escape(folded_term), folded_message))
    if not occurrences:
        return False
    for occurrence in occurrences:
        prefix = folded_message[max(0, occurrence.start() - 180) : occurrence.start()]
        boundary = max(prefix.rfind(mark) for mark in (".", "!", "?", ";", ":", "\n"))
        clause = prefix[boundary + 1 :]
        breaks = list(_NEGATION_BREAK.finditer(clause))
        if breaks:
            clause = clause[breaks[-1].end() :]
        negated_frames = list(_NEGATED_EVIDENCE_FRAME.finditer(clause))
        if not negated_frames:
            return True
        if _POSITIVE_EVIDENCE_FRAME.search(clause[negated_frames[-1].end() :]):
            return True
    return False


def allowed_url(value: object) -> bool:
    try:
        parsed = urllib.parse.urlsplit(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_SOURCE_HOSTS


def universal_failures(response: dict, capture_mode: str) -> list[str]:
    failures = []
    missing = sorted(REQUIRED_FIELDS.difference(response))
    if missing:
        failures.append("schema: missing fields " + ", ".join(missing))
        return failures
    if response.get("kind") not in {"answer", "clarify", "handoff", "privacy"}:
        failures.append(f"schema: invalid kind {response.get('kind')!r}")
    if not isinstance(response.get("message"), str) or not response["message"].strip():
        failures.append("schema: message must be non-empty text")
    elif len(response["message"].split()) > 48:
        failures.append("schema: message exceeds 48 words")
    if not isinstance(response.get("reason"), str):
        failures.append("schema: reason must be text")
    elif len(response["reason"].split()) > 18:
        failures.append("schema: reason exceeds 18 words")
    model_called = response.get("model_called")
    if not isinstance(model_called, bool):
        failures.append("schema: model_called must be boolean")
    elif response.get("kind") == "privacy":
        if model_called:
            failures.append("model: privacy response must not call the model")
    elif not model_called:
        failures.append("model: successful non-privacy response must call the model")
    if response.get("retrieval_scope") not in {"page", "site", "staff"}:
        failures.append("schema: invalid retrieval_scope")
    sources = response.get("sources")
    if not isinstance(sources, list):
        failures.append("schema: sources must be a list")
    elif response.get("kind") != "clarify" and not sources:
        failures.append("authority: a factual or handoff response needs a source")
    else:
        for source in sources:
            if not isinstance(source, dict) or not allowed_url(source.get("url")):
                failures.append("authority: source URL is outside the approved Fortune host")
                break
    related = response.get("related")
    if not isinstance(related, list):
        failures.append("schema: related must be a list")
    else:
        for item in related:
            if not isinstance(item, dict) or not allowed_url(item.get("url")):
                failures.append("authority: related URL is outside the approved Fortune host")
                break
    if not allowed_url(response.get("handoff_url")):
        failures.append("authority: handoff URL is outside the approved Fortune host")
    if not all(valid_uuid(response.get(key)) for key in ("conversation_id", "turn_id", "client_event_id")):
        failures.append("schema: conversation, turn, or client event ID is not a canonical UUID")
    message_ids = response.get("message_ids")
    if not isinstance(message_ids, dict) or not all(
        valid_uuid(message_ids.get(role)) for role in ("user", "assistant")
    ):
        failures.append("schema: user or assistant message ID is not a canonical UUID")
    capture = response.get("capture")
    if not isinstance(capture, dict):
        failures.append("capture: response capture metadata is missing")
    elif capture_mode == "none" and capture != {"mode": "none", "stored": False}:
        failures.append(f"capture: expected none/not-stored, got {capture!r}")
    elif capture_mode != "none" and not response.get("conversation_token"):
        failures.append("capture: continuation token is required when capture is enabled")
    return failures


def expected_failures(case: dict, status: int, response: dict, capture_mode: str) -> list[str]:
    expect = case["expect"]
    failures = []
    wanted_status = int(expect.get("status", 200))
    if status != wanted_status:
        failures.append(f"status: expected {wanted_status}, got {status}")
        return failures
    if status != 200:
        error_text = str(response.get("error", "")).casefold()
        terms = [str(value).casefold() for value in expect.get("error_contains_any", [])]
        if terms and not any(term in error_text for term in terms):
            failures.append(f"error: expected one of {terms!r}, got {error_text!r}")
        return failures

    failures.extend(universal_failures(response, capture_mode))
    checks = (
        ("kind", "kind_in"),
        ("retrieval_scope", "retrieval_scope_in"),
        ("request_language", "request_language_in"),
        ("response_language", "response_language_in"),
        ("chat_stage", "chat_stage_in"),
        ("request_kind", "request_kind_in"),
    )
    for response_key, expect_key in checks:
        allowed = expect.get(expect_key)
        if allowed and response.get(response_key) not in allowed:
            failures.append(
                f"{response_key}: expected one of {allowed!r}, got {response.get(response_key)!r}"
            )
    if "model_called" in expect and response.get("model_called") is not expect["model_called"]:
        failures.append(
            f"model_called: expected {expect['model_called']!r}, got {response.get('model_called')!r}"
        )
    if "choice_labels_exact" in expect:
        labels = [
            item.get("label")
            for item in response.get("choices", [])
            if isinstance(item, dict)
        ]
        if labels != expect["choice_labels_exact"]:
            failures.append(
                f"choices: expected {expect['choice_labels_exact']!r}, got {labels!r}"
            )
    blob = source_blob(response)
    authority_blob = (
        f"{blob} {choice_blob(response)}"
        if response.get("kind") == "clarify"
        and expect.get("clarify_authority_from_choices") is True
        else blob
    )
    source_terms = [str(value).casefold() for value in expect.get("source_match_any", [])]
    if source_terms and not any(blob_contains(authority_blob, term) for term in source_terms):
        failures.append(f"sources: none matched {source_terms!r}")
    excluded_source_terms = [
        str(value).casefold() for value in expect.get("source_excludes", [])
    ]
    for term in excluded_source_terms:
        if blob_contains(authority_blob, term):
            failures.append(f"sources: excluded term {term!r} was present")
    message = str(response.get("message", ""))
    folded_message = message.casefold()
    max_message_words = expect.get("max_message_words")
    if isinstance(max_message_words, int) and not isinstance(max_message_words, bool):
        message_words = len(message.split())
        if message_words > max_message_words:
            failures.append(
                f"message: exceeds case limit of {max_message_words} words; got {message_words}"
            )
    message_terms = [
        str(value).casefold() for value in expect.get("message_contains_any", [])
    ]
    if message_terms and not any(term in folded_message for term in message_terms):
        failures.append(f"message: none of {message_terms!r} was present")
    for value in expect.get("message_excludes", []):
        term = str(value).casefold()
        if message_has_unnegated_excluded_text(folded_message, term):
            failures.append(f"message: excluded text {value!r} was present")
    return failures


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 2)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((rate * (1 - rate) + z * z / (4 * total)) / total) / denominator
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


def threshold_score(rate: float) -> int:
    if rate >= 0.95:
        return 4
    if rate >= 0.90:
        return 3
    if rate >= 0.75:
        return 2
    if rate >= 0.50:
        return 1
    return 0


def kind_breakdown(results: list[dict], response_key: str) -> dict:
    grouped = {}
    for row in results:
        response = row.get("response") if isinstance(row.get("response"), dict) else {}
        value = response.get(response_key)
        if not isinstance(value, str) or not value:
            continue
        grouped.setdefault(value, []).append(row)
    output = {}
    for value, rows in sorted(grouped.items()):
        latencies = [row["latency_ms"] for row in rows if row.get("status")]
        word_counts = [
            len(str(row.get("response", {}).get("message", "")).split())
            for row in rows
        ]
        output[value] = {
            "total": len(rows),
            "passed": sum(bool(row.get("passed")) for row in rows),
            "latency_p50_ms": percentile(latencies, 0.50),
            "latency_p95_ms": percentile(latencies, 0.95),
            "latency_mean_ms": round(statistics.fmean(latencies), 2) if latencies else None,
            "message_words_mean": round(statistics.fmean(word_counts), 2) if word_counts else None,
            "message_words_max": max(word_counts) if word_counts else None,
            "model_calls": sum(
                bool(row.get("response", {}).get("model_called")) for row in rows
            ),
        }
    return output


def model_call_gate(results: list[dict]) -> dict:
    """Summarize the successful-turn model-use release contract.

    Privacy holds are the only successful responses exempt from a model call.
    Exact idempotent replays retain the original response's ``model_called``
    value, so they satisfy this response-level gate without implying a second
    provider request.
    """

    required = [
        row
        for row in results
        if row.get("status") == 200
        and row.get("response", {}).get("kind") != "privacy"
    ]
    called = [
        row
        for row in required
        if row.get("response", {}).get("model_called") is True
    ]
    skipped_ids = [
        row.get("id")
        for row in required
        if row.get("response", {}).get("model_called") is not True
    ]
    privacy_called_ids = [
        row.get("id")
        for row in results
        if row.get("status") == 200
        and row.get("response", {}).get("kind") == "privacy"
        and row.get("response", {}).get("model_called") is True
    ]
    return {
        "passed": not skipped_ids and not privacy_called_ids,
        "required_turns": len(required),
        "called_turns": len(called),
        "call_rate": round(len(called) / len(required), 4) if required else 1.0,
        "skipped_turn_ids": skipped_ids,
        "privacy_called_turn_ids": privacy_called_ids,
    }


def aggregate(results: list[dict], health_failures: list[str]) -> dict:
    required = [row for row in results if row["level"] in {"hard", "release"}]
    required_passes = sum(row["passed"] for row in required)
    required_rate = required_passes / len(required) if required else 0.0
    all_passes = sum(row["passed"] for row in results)
    slices = {}
    for row in results:
        entry = slices.setdefault(row["slice"], {"total": 0, "passed": 0, "required_total": 0, "required_passed": 0})
        entry["total"] += 1
        entry["passed"] += int(row["passed"])
        if row["level"] in {"hard", "release"}:
            entry["required_total"] += 1
            entry["required_passed"] += int(row["passed"])
    for entry in slices.values():
        entry["rate"] = round(entry["passed"] / entry["total"], 4)
        entry["required_rate"] = (
            round(entry["required_passed"] / entry["required_total"], 4)
            if entry["required_total"]
            else None
        )

    hard_failures = [row["id"] for row in results if row["level"] == "hard" and not row["passed"]]
    infrastructure = [
        row for row in results
        if row["transport_error"] or row["status"] == 429 or row["status"] >= 500
    ]
    latencies = [row["latency_ms"] for row in results if row["status"]]
    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)
    model_calls = sum(
        bool(row.get("response", {}).get("model_called")) for row in results
    )
    model_gate = model_call_gate(results)
    required_slice_rates = [
        entry["required_rate"]
        for entry in slices.values()
        if entry["required_total"] >= 2 and entry["required_rate"] is not None
    ]
    robustness_rate = min(required_slice_rates) if required_slice_rates else required_rate
    follow_up = [
        row for row in required if row["slice"] == "follow-up-context"
    ]
    follow_up_rate = (
        sum(row["passed"] for row in follow_up) / len(follow_up)
        if follow_up
        else 0.0
    )

    if not infrastructure and not any(
        failure.startswith("schema:")
        for row in results for failure in row["failures"]
    ):
        if p95 is not None and p95 < 5000:
            reliability = 4
        elif p95 is not None and p95 < 10000:
            reliability = 3
        elif p95 is not None and p95 < 20000:
            reliability = 2
        else:
            reliability = 1
    elif len(infrastructure) < len(results):
        reliability = 1
    else:
        reliability = 0

    if p95 is not None and p95 < 3000:
        efficiency = 4
    elif p95 is not None and p95 < 8000:
        efficiency = 3
    elif p95 is not None and p95 < 15000:
        efficiency = 2
    elif len(results) > 0:
        efficiency = 1
    else:
        efficiency = 0

    arena_scores = {
        "outcome_quality": threshold_score(required_rate),
        "robustness": threshold_score(robustness_rate),
        "reliability": reliability,
        "safety_integrity": (
            4 if not hard_failures and not health_failures and model_gate["passed"] else 0
        ),
        "efficiency": efficiency,
        "adaptation": threshold_score(follow_up_rate),
    }
    weights = {
        "outcome_quality": 0.35,
        "robustness": 0.20,
        "reliability": 0.15,
        "safety_integrity": 0.15,
        "efficiency": 0.10,
        "adaptation": 0.05,
    }
    composite = round(100 * sum(weights[key] * arena_scores[key] / 4 for key in weights), 1)
    required_slices_pass = all(
        entry["required_rate"] is None or entry["required_rate"] >= 0.75
        for entry in slices.values()
    )
    complete = len(results) >= 25 and all(row["status"] or row["transport_error"] for row in results)
    release_pass = (
        complete
        and not hard_failures
        and not health_failures
        and model_gate["passed"]
        and required_rate >= 0.90
        and required_slices_pass
    )
    return {
        "decision": "pass" if release_pass else "block",
        "run_complete": complete,
        "all_cases": {
            "passed": all_passes,
            "total": len(results),
            "rate": round(all_passes / len(results), 4) if results else 0.0,
        },
        "required_cases": {
            "passed": required_passes,
            "total": len(required),
            "rate": round(required_rate, 4),
            "wilson_95": wilson_interval(required_passes, len(required)),
        },
        "hard_gate": {
            "passed": not hard_failures and not health_failures and model_gate["passed"],
            "failed_cases": hard_failures,
            "health_failures": health_failures,
        },
        "model_call_gate": model_gate,
        "slices": slices,
        "operational": {
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "latency_mean_ms": round(statistics.fmean(latencies), 2) if latencies else None,
            "infrastructure_failures": len(infrastructure),
            "model_calls": model_calls,
            "model_call_rate": model_gate["call_rate"],
            "by_response_kind": kind_breakdown(results, "kind"),
            "by_request_kind": kind_breakdown(results, "request_kind"),
        },
        "arena": {
            "scale": [0, 4],
            "scores": arena_scores,
            "composite_100": composite,
            "hard_gate_overrides_composite": True,
        },
    }


def health_boundary_failures(health: dict, allow_capture: bool) -> list[str]:
    failures = []
    logging = health.get("conversation_logging")
    evaluation = health.get("evaluation")
    if not isinstance(logging, dict):
        return ["health: conversation_logging is missing"]
    mode = logging.get("capture_mode")
    if not allow_capture and mode != "none":
        failures.append(f"health: capture_mode must be none for this run; got {mode!r}")
    if mode == "none" and logging.get("enabled"):
        failures.append("health: capture reports enabled while mode is none")
    if mode == "none" and logging.get("database_configured"):
        failures.append("health: production capture-none unexpectedly has a database")
    if mode == "none" and isinstance(evaluation, dict) and evaluation.get("enabled"):
        failures.append("health: evaluation is enabled on the capture-none surface")
    return failures


def regrade(args: argparse.Namespace, suite: dict, cases_path: pathlib.Path, spec_path: pathlib.Path) -> int:
    input_path = pathlib.Path(args.regrade_input).resolve()
    original = load_json(input_path)
    expected_hash = original.get("lineage", {}).get("cases_sha256")
    actual_hash = file_hash(cases_path)
    if expected_hash != actual_hash:
        print(
            "error: frozen cases changed since the original run; refusing to regrade",
            file=sys.stderr,
        )
        return 2
    case_by_id = {case["id"]: case for case in suite["cases"]}
    health = original.get("target", {}).get("health", {})
    health_failures = health_boundary_failures(health, args.allow_capture)
    capture_mode = health.get("conversation_logging", {}).get("capture_mode", "unknown")
    results = original.get("results")
    if not isinstance(results, list):
        print("error: original run has no results", file=sys.stderr)
        return 2
    for row in results:
        case = case_by_id.get(row.get("id"))
        if not case:
            print(f"error: unknown result case {row.get('id')!r}", file=sys.stderr)
            return 2
        transport_error = row.get("transport_error")
        failures = (
            [f"transport: {transport_error}"]
            if transport_error
            else expected_failures(
                case,
                int(row.get("status") or 0),
                row.get("response") if isinstance(row.get("response"), dict) else {},
                capture_mode,
            )
        )
        row["failures"] = failures
        row["passed"] = not failures
    original["aggregate"] = aggregate(results, health_failures)
    original["created_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    original["regrade"] = {
        "parent_record": str(input_path),
        "parent_sha256": file_hash(input_path),
        "reason": "Allow absent conversation_token when capture_mode is none; raw episodes and case expectations are unchanged.",
    }
    original["lineage"]["runner_sha256"] = file_hash(pathlib.Path(__file__).resolve())
    original["lineage"]["spec_sha256"] = file_hash(spec_path)
    output_path = pathlib.Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(original, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    required = original["aggregate"]["required_cases"]
    print(
        f"{original['aggregate']['decision'].upper()}: "
        f"{required['passed']}/{required['total']} required; "
        f"ARENA {original['aggregate']['arena']['composite_100']}/100"
    )
    print(f"record: {output_path}")
    return 0 if original["aggregate"]["decision"] == "pass" else 1


def run(args: argparse.Namespace) -> int:
    cases_path = pathlib.Path(args.cases).resolve()
    spec_path = pathlib.Path(args.spec).resolve()
    suite = load_json(cases_path)
    spec = load_json(spec_path)
    try:
        suite = apply_grader_overrides(suite, spec, unit_kind="cases")
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    validation_errors = validate_suite(suite)
    if validation_errors:
        for error in validation_errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        f"valid: {len(suite['cases'])} cases across "
        f"{len({case['slice'] for case in suite['cases']})} slices"
    )
    if args.validate_only:
        return 0
    if args.regrade_input:
        return regrade(args, suite, cases_path, spec_path)
    if not args.base_url:
        print("error: --base-url is required unless --validate-only is used", file=sys.stderr)
        return 2

    base_url = args.base_url.rstrip("/")
    health_status, health, health_latency, health_transport_error = json_request(
        base_url + "/health", timeout=args.timeout
    )
    health_failures = []
    if health_transport_error:
        health_failures.append(f"health: transport error {health_transport_error}")
    if health_status != 200:
        health_failures.append(f"health: expected 200, got {health_status}")
    health_failures.extend(health_boundary_failures(health, args.allow_capture))
    capture_mode = (
        health.get("conversation_logging", {}).get("capture_mode", "unknown")
        if isinstance(health, dict)
        else "unknown"
    )
    if health_failures and not args.continue_on_health_failure:
        for failure in health_failures:
            print(f"error: {failure}", file=sys.stderr)
        print("refusing to send synthetic cases across an unverified data boundary", file=sys.stderr)
        return 3

    default_page = suite.get("default_page_context", {})
    results = []
    for index, case in enumerate(suite["cases"], start=1):
        message = expanded_message(case)
        payload = {
            "message": message,
            "page_context": case.get("page_context", default_page),
            "history": case.get("history", []),
            "client_event_id": str(uuid.uuid4()),
            "client_surface": "benchmark",
            "automation_source": "fixed-suite",
        }
        status, response, latency_ms, transport_error = json_request(
            base_url + "/api/chat",
            method="POST",
            payload=payload,
            timeout=args.timeout,
        )
        if (transport_error or status >= 500) and args.retry_transient:
            time.sleep(args.retry_delay)
            status, response, latency_ms, transport_error = json_request(
                base_url + "/api/chat",
                method="POST",
                payload=payload,
                timeout=args.timeout,
            )
        failures = (
            [f"transport: {transport_error}"]
            if transport_error
            else expected_failures(case, status, response, capture_mode)
        )
        row = {
            "id": case["id"],
            "slice": case["slice"],
            "level": case["level"],
            "message": message,
            "page_context": payload["page_context"],
            "history": payload["history"],
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "transport_error": transport_error,
            "passed": not failures,
            "failures": failures,
            "response": artifact_response(response),
        }
        results.append(row)
        marker = "PASS" if row["passed"] else "FAIL"
        print(f"[{index:02d}/{len(suite['cases'])}] {marker} {case['id']} ({latency_ms:.0f} ms)")
        if status == 429:
            print("rate limit reached; stopping to avoid affecting other visitors", file=sys.stderr)
            break
        if args.delay:
            time.sleep(args.delay)

    aggregate_result = aggregate(results, health_failures)
    timestamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    record = {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "created_at": timestamp,
        "suite": suite.get("suite_id"),
        "suite_version": spec.get("identity", {}).get("version"),
        "target": {
            "base_url": base_url,
            "health_status": health_status,
            "health_latency_ms": round(health_latency, 2),
            "health": health,
            "local_git_commit": git_value("rev-parse", "HEAD"),
            "local_git_status": git_value("status", "--short"),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "lineage": {
            "cases_sha256": file_hash(cases_path),
            "spec_sha256": file_hash(spec_path),
            "runner_sha256": file_hash(pathlib.Path(__file__).resolve()),
        },
        "protocol": {
            "timeout_seconds": args.timeout,
            "delay_seconds": args.delay,
            "retry_transient": args.retry_transient,
            "capture_allowed": args.allow_capture,
            "client_surface": "benchmark",
        },
        "aggregate": aggregate_result,
        "results": results,
    }
    output_path = pathlib.Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    required = aggregate_result["required_cases"]
    print(
        f"{aggregate_result['decision'].upper()}: "
        f"{required['passed']}/{required['total']} required; "
        f"ARENA {aggregate_result['arena']['composite_100']}/100"
    )
    print(f"record: {output_path}")
    return 0 if aggregate_result["decision"] == "pass" else 1


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--base-url", default="")
    value.add_argument("--cases", default=str(DEFAULT_CASES))
    value.add_argument("--spec", default=str(DEFAULT_SPEC))
    value.add_argument("--output", default=str(ROOT / "evals" / "website-guide" / "results" / "latest.json"))
    value.add_argument("--timeout", type=float, default=30)
    value.add_argument("--delay", type=float, default=0.15)
    value.add_argument("--retry-transient", type=int, choices=(0, 1), default=1)
    value.add_argument("--retry-delay", type=float, default=1.0)
    value.add_argument("--validate-only", action="store_true")
    value.add_argument("--regrade-input", default="")
    value.add_argument("--allow-capture", action="store_true")
    value.add_argument("--continue-on-health-failure", action="store_true")
    return value


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
