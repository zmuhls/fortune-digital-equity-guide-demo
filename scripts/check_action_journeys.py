#!/usr/bin/env python3
"""Live-model action checks; refuses any server that could store test transcripts."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


JOURNEYS = [
    ("schedule-home", "/", [
        ("Where and when are current classes?", ["/calendar"]),
        ("How can I register?", ["/calendar", "/contact", "/"], ["/calendar"]),
    ]),
    ("schedule-on-calendar", "/calendar", [
        ("Where and when are current classes?", ["/calendar"]),
        ("Where do I sign up?", ["/calendar", "/contact", "/"], ["/calendar"]),
    ]),
    ("registration-followups", "/", [
        ("How can i register", ["/calendar", "/contact", "/"], ["/calendar"]),
        ("Idk", None),
        ("okay can i see what u got", ["/calendar", "/workshops"]),
        ("how bout tomorrow", ["/calendar"]),
        ("whaat's that about", ["/service-page/capstone-activity-using-ai"]),
    ]),
    ("email-description-and-booking", "/", [
        ("I want to learn email from the beginning. What class fits?", ["/service-page/intro-to-email"]),
        ("What would I learn there?", ["/service-page/intro-to-email"]),
        ("How do I register for that?", ["/calendar", "/contact", "/service-page/intro-to-email"], ["/calendar"]),
    ]),
    ("individual-tutoring", "/support", [
        ("Can someone help me one on one with my computer?", ["/support", "/contact"]),
        ("How do I arrange that session?", ["/contact", "/support"], ["/contact"]),
        ("What are the hours for the one-on-one appointments?", ["/contact", "/support", "/calendar"]),
    ]),
    ("open-computer-lab", "/", [
        ("Do I have to make an appointment for the open computer lab?", ["/support", "/calendar"]),
        ("Where do I find the next session?", ["/calendar", "/support"], ["/calendar"]),
    ]),
    ("walk-in-versus-signup", "/contact", [
        ("Do I need to register for a regular class in order to attend?", ["/contact", "/calendar"]),
        ("I still want to register. Where?", ["/calendar", "/contact", "/"], ["/calendar"]),
    ]),
    ("device-program", "/", [
        ("Do I automatically qualify for a laptop as a Fortune participant?", ["/", "/contact", "/devices"]),
        ("Where can I read the device requirements?", ["/devices"], ["/devices"]),
    ]),
    ("site-purpose-to-programs", "/", [
        ("what is digital equity", ["/", "/about", "/service-page/intro-to-fortune-digital-equity"]),
        ("okay can I see the classes", ["/workshops", "/calendar"], ["/workshops", "/calendar"]),
        ("and sign up", ["/calendar", "/contact", "/"], ["/calendar"]),
    ]),
    ("spanish-signup", "/", [
        ("¿Dónde puedo inscribirme en una clase?", ["/calendar", "/contact", "/"], ["/calendar"]),
        ("¿Tengo que registrarme para asistir?", ["/contact", "/calendar"]),
    ]),
    ("registration-after-contact-faq", "/contact", [
        ("Do I need to attend every scheduled class in a month?", ["/contact", "/"]),
        ("Show me where I can actually register for a class.", ["/calendar", "/contact", "/"], ["/calendar"]),
        ("Do regular classes also accept walk-ins?", ["/contact", "/calendar", "/"]),
        ("I want to register ahead anyway. Take me to the signup page.", ["/calendar", "/contact", "/"], ["/calendar"]),
    ]),
]


def public_source_path(row):
    """A matching path on an unrelated host must never satisfy this audit."""
    if not isinstance(row, dict):
        return None
    parsed = urllib.parse.urlsplit(row.get("url", ""))
    if parsed.scheme != "https" or parsed.hostname not in {"www.fortunedigitalequity.org", "fortunedigitalequity.org"}:
        return None
    return parsed.path.rstrip("/") or "/"


def visible_button(response):
    """Use the rendered button rule, not any convenient later source/link."""
    if response.get("action") is not None:
        return response["action"]
    sources = response.get("sources", [])
    return sources[0] if sources else None


def destination_failures(response, source_paths, action_paths=None):
    failures = []
    actual_sources = [public_source_path(row) for row in response.get("sources", [])]
    if source_paths and not any(path in source_paths for path in actual_sources):
        failures.append("missing expected factual source")
    if action_paths and public_source_path(visible_button(response)) not in action_paths:
        failures.append("visible action button has the wrong destination")
    return failures


def request(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={
        "Content-Type": "application/json", "User-Agent": "FortuneActionJourneyQA/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def run_journey(base, journey):
    name, page, turns = journey
    history, results = [], []
    for question, source_paths, *action_expectation in turns:
        action_paths = action_expectation[0] if action_expectation else None
        started = time.monotonic()
        status, response = request(base, "/api/chat", {
            "message": question, "history": history[-16:],
            "page_context": {"url": "https://www.fortunedigitalequity.org" + page},
            "client_surface": "benchmark", "automation_source": "action-journey-qa",
            "conversation_id": str(uuid.uuid4()), "client_event_id": str(uuid.uuid4()),
        })
        failures = destination_failures(response, source_paths, action_paths)
        if status != 200 or response.get("model_called") is not True:
            failures.append("live model did not return a successful answer")
        if response.get("capture") != {"mode": "none", "stored": False}:
            failures.append("test capture is not disabled")
        if name == "registration-followups" and question == "whaat's that about":
            answer = response.get("message", "").lower()
            if "tba" in answer or "to be announced" in answer:
                failures.append("existing class description was incorrectly reported as unavailable")
        result = {
            "question": question, "status": status,
            "answer": response.get("message", response.get("error")),
            "kind": response.get("kind"), "sources": response.get("sources", []),
            "related": response.get("related", []),
            "action": response.get("action"), "visible_button": visible_button(response),
            "expected_source_paths": source_paths, "expected_action_paths": action_paths,
            "model_called": response.get("model_called"),
            "policy": response.get("prompt_policy_version"),
            "seconds": round(time.monotonic() - started, 2), "failures": failures,
        }
        results.append(result)
        print(json.dumps({"journey": name, **result}, ensure_ascii=False), flush=True)
        if status == 200:
            history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": response["message"]}])
    return {"name": name, "page": page, "turns": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    status, health = request(args.base_url, "/health")
    if status != 200 or health.get("conversation_logging", {}).get("capture_mode") != "none":
        raise SystemExit("Refusing test: server must be healthy with capture_mode=none.")
    with ThreadPoolExecutor(max_workers=3) as workers:
        results = list(workers.map(lambda journey: run_journey(args.base_url, journey), JOURNEYS))
    report = {"model": health.get("model"), "capture_mode": "none", "journeys": results,
              "completed_at_utc": datetime.now(timezone.utc).isoformat()}
    report["failed_turns"] = sum(bool(turn["failures"]) for journey in results for turn in journey["turns"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"journeys": len(results), "failed_turns": report["failed_turns"], "output": str(args.output)}))
    return bool(report["failed_turns"])


if __name__ == "__main__":
    raise SystemExit(main())
