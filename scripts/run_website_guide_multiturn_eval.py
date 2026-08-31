#!/usr/bin/env python3
"""Run stateful, synthetic Website Guide retrieval conversations."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import platform
import re
import statistics
import time
import uuid

try:
    from scripts import run_website_guide_eval as core
except ModuleNotFoundError:  # Direct execution from the repository root.
    import run_website_guide_eval as core


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evals" / "website-guide" / "multiturn-cases.json"
DEFAULT_SPEC = ROOT / "evals" / "website-guide" / "multiturn-spec.json"
MAX_HISTORY_MESSAGES = 12
LEVELS = {"hard", "release", "diagnostic"}


def validate_suite(document: dict) -> list[str]:
    errors: list[str] = []
    episodes = document.get("episodes")
    if not isinstance(episodes, list):
        return ["episodes must be a list"]
    if len(episodes) < 10:
        errors.append(f"suite must contain at least 10 episodes; got {len(episodes)}")
    episode_ids: set[str] = set()
    turn_ids: set[str] = set()
    total_turns = 0
    contextual_turns = 0
    for episode_index, episode in enumerate(episodes):
        prefix = f"episodes[{episode_index}]"
        if not isinstance(episode, dict):
            errors.append(f"{prefix} must be an object")
            continue
        episode_id = episode.get("id")
        if not isinstance(episode_id, str) or not episode_id:
            errors.append(f"{prefix}.id must be non-empty text")
        elif episode_id in episode_ids:
            errors.append(f"duplicate episode id: {episode_id}")
        else:
            episode_ids.add(episode_id)
        if episode.get("level") not in LEVELS:
            errors.append(f"{prefix}.level must be one of {sorted(LEVELS)}")
        if not isinstance(episode.get("slice"), str) or not episode.get("slice"):
            errors.append(f"{prefix}.slice must be non-empty text")
        turns = episode.get("turns")
        if not isinstance(turns, list):
            errors.append(f"{prefix}.turns must be a list")
            continue
        if len(turns) < 4:
            errors.append(f"{prefix} must contain at least 4 turns; got {len(turns)}")
        total_turns += len(turns)
        for turn_index, turn in enumerate(turns):
            turn_prefix = f"{prefix}.turns[{turn_index}]"
            if not isinstance(turn, dict):
                errors.append(f"{turn_prefix} must be an object")
                continue
            turn_id = turn.get("id")
            qualified_id = f"{episode_id}/{turn_id}"
            if not isinstance(turn_id, str) or not turn_id:
                errors.append(f"{turn_prefix}.id must be non-empty text")
            elif qualified_id in turn_ids:
                errors.append(f"duplicate turn id: {qualified_id}")
            else:
                turn_ids.add(qualified_id)
            if not isinstance(turn.get("message"), str) or not turn["message"].strip():
                errors.append(f"{turn_prefix}.message must be non-empty text")
            if not isinstance(turn.get("expect"), dict):
                errors.append(f"{turn_prefix}.expect must be an object")
            if turn.get("mode") in {"deictic", "elliptical", "topic_shift"}:
                contextual_turns += 1
    if total_turns < 40:
        errors.append(f"suite must contain at least 40 turns; got {total_turns}")
    if contextual_turns < 10:
        errors.append(
            f"suite must contain at least 10 contextual retrieval turns; got {contextual_turns}"
        )
    return errors


def continuity_failures(
    *,
    turn_index: int,
    response: dict,
    conversation_id: str,
    history: list[dict],
    history_limit: int = MAX_HISTORY_MESSAGES,
) -> list[str]:
    failures: list[str] = []
    expected_stage = "opening" if turn_index == 0 else "follow_up"
    if response.get("chat_stage") != expected_stage:
        failures.append(
            f"continuity: expected {expected_stage} chat stage, got {response.get('chat_stage')!r}"
        )
    received_id = str(response.get("conversation_id") or "")
    if conversation_id and received_id != conversation_id:
        failures.append(
            f"continuity: conversation changed from {conversation_id} to {received_id or '<missing>'}"
        )
    expected_history = min(turn_index * 2, history_limit)
    if len(history) != expected_history:
        failures.append(
            f"continuity: expected {expected_history} history messages, got {len(history)}"
        )
    return failures


def explicit_prior_detail_request(question: str, prior_answer: str) -> bool:
    """Recognize a bounded request to repeat a detail already in the latest answer."""

    value = str(question or "").casefold().replace("’", "'").strip()
    prior = str(prior_answer or "").casefold().replace("’", "'").strip()
    if not value or not prior or re.search(
        r"\b(?:what else|anything else|tell me more|what next|what more|"
        r"more details?|go on|continue)\b",
        value,
    ):
        return False

    if re.search(
        r"\b(?:how (?:do|can|could|would|will) (?:i|we) qualify|"
        r"what (?:do|would) (?:i|we) need to (?:do )?(?:qualify|be eligible)|"
        r"(?:am i|are we) eligible)\b",
        value,
    ):
        return bool(re.search(r"\b(?:qualif\w*|eligib\w*|requirements?)\b", prior))

    if re.search(
        r"\b(?:what|which)(?: [a-z0-9'-]+){0,3} (?:does|do|did|will|would) "
        r"(?:(?:that|this|the) (?:class|course|workshop|program|service|page|option)|"
        r"it|they) (?:cover|include|teach|offer|mean|say)\b",
        value,
    ):
        if not re.search(
            r"\b(?:cover\w*|includ\w*|teach\w*|learn\w*|practic\w*|show\w*|"
            r"topics?|skills?)\b",
            prior,
        ):
            return False
        generic = {
            "what", "which", "does", "do", "did", "will", "would", "that",
            "this", "the", "class", "course", "workshop", "program", "service",
            "page", "option", "it", "they", "cover", "include", "teach", "offer",
            "mean", "say", "technique", "techniques", "topic", "topics", "detail",
            "details",
        }
        requested = set(re.findall(r"[a-z0-9]+", value)).difference(generic)
        return not requested or bool(requested.intersection(re.findall(r"[a-z0-9]+", prior)))

    if re.search(
        r"\b(?:can|could|would) you (?:repeat|restate) (?:that|this|it)\b|"
        r"\b(?:is that (?:right|correct)|did you say\b)",
        value,
    ):
        return True
    return False


def advancement_failures(
    *,
    response: dict,
    history: list[dict],
    question: str = "",
    required: bool = True,
) -> list[str]:
    """Reject a factual follow-up only when all substantive evidence is reused."""

    if not required or response.get("kind") != "answer" or not history:
        return []
    current = str(response.get("message") or "")
    if current in {
        "I couldn’t confirm that on Fortune’s public pages.",
        "No pude confirmarlo en las páginas públicas de Fortune.",
    }:
        return []
    def sentence_terms(value: str) -> list[set[str]]:
        return [
            set(terms)
            for sentence in re.split(r"(?<=[.!?])\s+", value)
            if len(terms := re.findall(r"[a-z0-9]+", sentence.casefold())) >= 6
        ]

    prior_sentences = sentence_terms(
        " ".join(
            str(item.get("content") or "")
            for item in history
            if item.get("role") == "assistant"
        )
    )
    current_sentences = sentence_terms(current)
    if not current_sentences or not prior_sentences:
        return []
    prior_answer = next(
        (
            str(item.get("content") or "")
            for item in reversed(history)
            if item.get("role") == "assistant"
        ),
        "",
    )

    def repeats_prior(current_terms: set[str]) -> bool:
        return any(
            len(current_terms.intersection(prior_terms))
            / len(current_terms)
            >= 0.85
            for prior_terms in prior_sentences
        )

    if (
        all(repeats_prior(sentence) for sentence in current_sentences)
        and not explicit_prior_detail_request(question, prior_answer)
    ):
        return ["continuity: answer repeats prior evidence instead of advancing"]
    return []


def percentile(values: list[float], fraction: float) -> float | None:
    return core.percentile(values, fraction)


def aggregate(episodes: list[dict], health_failures: list[str]) -> dict:
    turns = [turn for episode in episodes for turn in episode["turns"]]
    required_episodes = [
        episode for episode in episodes if episode["level"] in {"hard", "release"}
    ]
    required_passes = sum(bool(episode["passed"]) for episode in required_episodes)
    required_rate = required_passes / len(required_episodes) if required_episodes else 0.0
    hard_failures = [
        episode["id"]
        for episode in episodes
        if episode["level"] == "hard" and not episode["passed"]
    ]
    contextual = [
        turn
        for turn in turns
        if turn.get("mode") in {"deictic", "elliptical", "topic_shift"}
    ]
    contextual_passes = sum(bool(turn["passed"]) for turn in contextual)
    contextual_rate = contextual_passes / len(contextual) if contextual else 0.0
    successful_latencies = [turn["latency_ms"] for turn in turns if turn.get("status")]
    infrastructure = [
        turn
        for turn in turns
        if turn.get("transport_error") or turn.get("status") == 429 or turn.get("status", 0) >= 500
    ]
    model_calls = sum(bool(turn.get("response", {}).get("model_called")) for turn in turns)
    model_gate = core.model_call_gate(turns)
    slices: dict[str, dict] = {}
    for episode in episodes:
        row = slices.setdefault(episode["slice"], {"episodes": 0, "passed": 0})
        row["episodes"] += 1
        row["passed"] += int(episode["passed"])
    for row in slices.values():
        row["rate"] = round(row["passed"] / row["episodes"], 4)
    required_slices_pass = all(row["rate"] >= 0.75 for row in slices.values())
    complete = len(episodes) >= 10 and len(turns) >= 40 and all(
        turn.get("status") or turn.get("transport_error") for turn in turns
    )
    decision_pass = (
        complete
        and not health_failures
        and not hard_failures
        and not infrastructure
        and model_gate["passed"]
        and required_rate >= 0.90
        and contextual_rate >= 0.85
        and required_slices_pass
    )
    return {
        "decision": "pass" if decision_pass else "block",
        "run_complete": complete,
        "episodes": {
            "passed": sum(bool(episode["passed"]) for episode in episodes),
            "total": len(episodes),
        },
        "required_episodes": {
            "passed": required_passes,
            "total": len(required_episodes),
            "rate": round(required_rate, 4),
            "wilson_95": core.wilson_interval(required_passes, len(required_episodes)),
        },
        "turns": {
            "passed": sum(bool(turn["passed"]) for turn in turns),
            "total": len(turns),
            "contextual_passed": contextual_passes,
            "contextual_total": len(contextual),
            "contextual_rate": round(contextual_rate, 4),
        },
        "hard_gate": {
            "passed": not hard_failures and not health_failures and model_gate["passed"],
            "failed_episodes": hard_failures,
            "health_failures": health_failures,
        },
        "model_call_gate": model_gate,
        "slices": slices,
        "operational": {
            "latency_p50_ms": percentile(successful_latencies, 0.50),
            "latency_p95_ms": percentile(successful_latencies, 0.95),
            "latency_mean_ms": (
                round(statistics.fmean(successful_latencies), 2)
                if successful_latencies
                else None
            ),
            "infrastructure_failures": len(infrastructure),
            "model_calls": model_calls,
            "model_call_rate": model_gate["call_rate"],
        },
    }


def run(args: argparse.Namespace) -> int:
    cases_path = pathlib.Path(args.cases).resolve()
    spec_path = pathlib.Path(args.spec).resolve()
    suite = core.load_json(cases_path)
    spec = core.load_json(spec_path)
    try:
        suite = core.apply_grader_overrides(suite, spec, unit_kind="turns")
    except ValueError as error:
        print(f"error: {error}")
        return 2
    errors = validate_suite(suite)
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 2
    episode_count = len(suite["episodes"])
    turn_count = sum(len(episode["turns"]) for episode in suite["episodes"])
    print(f"valid: {episode_count} episodes, {turn_count} turns")
    if args.validate_only:
        return 0
    if not args.base_url:
        print("error: --base-url is required unless --validate-only is used")
        return 2

    base_url = args.base_url.rstrip("/")
    health_status, health, health_latency, health_error = core.json_request(
        base_url + "/health", timeout=args.timeout
    )
    health_failures: list[str] = []
    if health_error:
        health_failures.append(f"health: transport error {health_error}")
    if health_status != 200:
        health_failures.append(f"health: expected 200, got {health_status}")
    health_failures.extend(
        core.health_boundary_failures(health, allow_capture=args.allow_capture)
    )
    if health_failures:
        for failure in health_failures:
            print(f"error: {failure}")
        print("refusing to send synthetic episodes across an unverified data boundary")
        return 3
    capture_mode = health.get("conversation_logging", {}).get("capture_mode", "unknown")

    default_page = suite.get("default_page_context", {})
    episode_results: list[dict] = []
    for episode_number, episode in enumerate(suite["episodes"], start=1):
        history: list[dict] = []
        conversation_id = ""
        conversation_token = ""
        turn_results: list[dict] = []
        print(f"[{episode_number:02d}/{episode_count}] {episode['id']}")
        for turn_index, turn in enumerate(episode["turns"]):
            page_context = turn.get(
                "page_context", episode.get("page_context", default_page)
            )
            event_id = str(uuid.uuid4())
            payload = {
                "message": turn["message"],
                "page_context": page_context,
                "history": list(history),
                "client_event_id": event_id,
                "client_surface": "benchmark",
                "automation_source": "multiturn-suite",
            }
            if conversation_id:
                payload["conversation_id"] = conversation_id
            if conversation_token:
                payload["conversation_token"] = conversation_token
            status, response, latency_ms, transport_error = core.json_request(
                base_url + "/api/chat",
                method="POST",
                payload=payload,
                timeout=args.timeout,
            )
            if (transport_error or status >= 500) and args.retry_transient:
                time.sleep(args.retry_delay)
                status, response, latency_ms, transport_error = core.json_request(
                    base_url + "/api/chat",
                    method="POST",
                    payload=payload,
                    timeout=args.timeout,
                )
            failures = (
                [f"transport: {transport_error}"]
                if transport_error
                else core.expected_failures(turn, status, response, capture_mode)
            )
            if not transport_error and status == 200:
                failures.extend(
                    continuity_failures(
                        turn_index=turn_index,
                        response=response,
                        conversation_id=conversation_id,
                        history=history,
                    )
                )
                if turn_index:
                    failures.extend(
                        advancement_failures(
                            response=response,
                            history=history,
                            question=turn["message"],
                            required=turn.get("expect", {}).get(
                                "advancement_required", True
                            ),
                        )
                    )
            row = {
                "id": turn["id"],
                "mode": turn.get("mode", "explicit"),
                "message": turn["message"],
                "page_context": page_context,
                "history_sent": list(history),
                "conversation_id_sent": conversation_id or None,
                "client_event_id_sent": event_id,
                "status": status,
                "latency_ms": round(latency_ms, 2),
                "transport_error": transport_error,
                "passed": not failures,
                "failures": failures,
                "response": core.artifact_response(response),
            }
            turn_results.append(row)
            marker = "PASS" if row["passed"] else "FAIL"
            print(
                f"  {turn_index + 1:02d} {marker} {turn['id']} "
                f"({latency_ms:.0f} ms, {response.get('kind', 'no-kind')}, "
                f"model={bool(response.get('model_called'))})"
            )
            if status == 200 and isinstance(response.get("message"), str):
                history = (
                    history
                    + [
                        {"role": "user", "content": turn["message"]},
                        {"role": "assistant", "content": response["message"]},
                    ]
                )[-MAX_HISTORY_MESSAGES:]
                conversation_id = str(response.get("conversation_id") or conversation_id)
                conversation_token = str(
                    response.get("conversation_token") or conversation_token
                )
            if status == 429:
                print("rate limit reached; stopping to avoid affecting other visitors")
                break
            if args.delay:
                time.sleep(args.delay)
        episode_result = {
            "id": episode["id"],
            "slice": episode["slice"],
            "level": episode["level"],
            "passed": len(turn_results) == len(episode["turns"])
            and all(turn["passed"] for turn in turn_results),
            "turns": turn_results,
        }
        episode_results.append(episode_result)
        if len(turn_results) != len(episode["turns"]):
            break

    aggregate_result = aggregate(episode_results, health_failures)
    record = {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "created_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "suite": suite.get("suite_id"),
        "suite_version": spec.get("identity", {}).get("version"),
        "target": {
            "base_url": base_url,
            "health_status": health_status,
            "health_latency_ms": round(health_latency, 2),
            "health": health,
            "local_git_commit": core.git_value("rev-parse", "HEAD"),
            "local_git_status": core.git_value("status", "--short"),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "lineage": {
            "cases_sha256": core.file_hash(cases_path),
            "spec_sha256": core.file_hash(spec_path),
            "runner_sha256": core.file_hash(pathlib.Path(__file__).resolve()),
        },
        "protocol": {
            "timeout_seconds": args.timeout,
            "delay_seconds": args.delay,
            "retry_transient": bool(args.retry_transient),
            "history_messages": MAX_HISTORY_MESSAGES,
            "capture_allowed": args.allow_capture,
            "client_surface": "benchmark",
        },
        "aggregate": aggregate_result,
        "episodes": episode_results,
    }
    output_path = pathlib.Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    required = aggregate_result["required_episodes"]
    turns = aggregate_result["turns"]
    print(
        f"{aggregate_result['decision'].upper()}: "
        f"{required['passed']}/{required['total']} required episodes; "
        f"{turns['passed']}/{turns['total']} turns; "
        f"{turns['contextual_passed']}/{turns['contextual_total']} contextual"
    )
    print(f"record: {output_path}")
    return 0 if aggregate_result["decision"] == "pass" else 1


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--base-url", default="")
    value.add_argument("--cases", default=str(DEFAULT_CASES))
    value.add_argument("--spec", default=str(DEFAULT_SPEC))
    value.add_argument(
        "--output",
        default=str(
            ROOT / "evals" / "website-guide" / "results" / "multiturn-latest.json"
        ),
    )
    value.add_argument("--timeout", type=float, default=30)
    value.add_argument("--delay", type=float, default=0.15)
    value.add_argument("--retry-transient", type=int, choices=(0, 1), default=1)
    value.add_argument("--retry-delay", type=float, default=1.0)
    value.add_argument(
        "--allow-capture",
        action="store_true",
        help="allow benchmark turns in an explicitly approved capture environment",
    )
    value.add_argument("--validate-only", action="store_true")
    return value


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
