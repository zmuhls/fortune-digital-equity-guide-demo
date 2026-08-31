#!/usr/bin/env python3
"""Exercise conversation capture without adding a reviewer-visible transcript."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
import uuid


def get_json(base_url: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def post(base_url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def require_uuid(value: object) -> str:
    parsed = str(uuid.UUID(str(value)))
    if parsed != value:
        raise AssertionError(f"Expected canonical UUID, received {value!r}")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    args = parser.parse_args()

    health_status, health = get_json(args.base_url, "/health")
    prompt_policy_version = health.get("prompt_policy", {}).get("version")
    if health_status != 200 or not prompt_policy_version:
        raise AssertionError("Health response does not expose a prompt-policy version")

    event_id = str(uuid.uuid4())
    first_payload = {
        "message": "What does the Digital Equity Program offer?",
        "client_surface": "benchmark",
        "automation_source": "capture-verification",
        "client_event_id": event_id,
        "page_context": {
            "url": "https://www.fortunedigitalequity.org/",
            "path": "/",
            "title": "Synthetic Program Test",
        },
    }
    first_status, first = post(args.base_url, first_payload)
    replay_status, replay = post(args.base_url, first_payload)
    conflict_status, _ = post(args.base_url, {
        **first_payload,
        "message": "class",
    })

    privacy_event_id = str(uuid.uuid4())
    privacy_status, privacy = post(args.base_url, {
        "message": "My synthetic Fortune ID is 654321",
        "client_surface": "benchmark",
        "client_event_id": privacy_event_id,
        "page_context": first_payload["page_context"],
    })

    if (first_status, replay_status, conflict_status, privacy_status) != (200, 200, 409, 200):
        raise AssertionError("Unexpected capture verification status sequence")
    for key in ("conversation_id", "turn_id", "client_event_id"):
        require_uuid(first[key])
    for value in first["message_ids"].values():
        require_uuid(value)
    if first["turn_id"] != replay["turn_id"] or first["message_ids"] != replay["message_ids"]:
        raise AssertionError("Idempotent replay changed persisted identifiers")
    if first.get("kind") == "privacy" or first.get("model_called") is not True:
        raise AssertionError("Ordinary safe capture prompt did not call the model")
    if replay.get("model_called") is not True:
        raise AssertionError("Idempotent replay did not preserve model-call metadata")
    if first.get("capture") != {"mode": "transcript", "stored": True}:
        raise AssertionError("Transcript capture is not active")
    expected_context = {
        "chat_stage": "opening",
        "request_kind": "retrieval",
        "request_language": "en",
        "response_language": "en",
        "prompt_policy_version": prompt_policy_version,
    }
    if {key: first.get(key) for key in expected_context} != expected_context:
        raise AssertionError("Interaction context was not logged consistently")
    if privacy.get("kind") != "privacy":
        raise AssertionError("Synthetic personal-information sentinel was not held")
    if privacy.get("model_called") is not False:
        raise AssertionError("Privacy hold unexpectedly called the model")

    print(json.dumps({
        "clear": {
            "conversation_id": first["conversation_id"],
            "turn_id": first["turn_id"],
            "client_event_id": event_id,
            "replay_turn_id": replay["turn_id"],
        },
        "conflicting_event_status": conflict_status,
        "privacy": {
            "conversation_id": privacy["conversation_id"],
            "turn_id": privacy["turn_id"],
            "client_event_id": privacy_event_id,
            "kind": privacy["kind"],
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
