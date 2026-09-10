"""Regressions from the September evaluation audit; no live providers or data."""
import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import server
from evaluation_store import EvaluationStore
from live_calendar import LiveCalendarCache


class RevisionSweepTests(unittest.TestCase):
    def test_signup_retains_coursera_including_bare_register(self):
        history = [{"role": "user", "content": "How can I get a free Coursera account?"},
                   {"role": "assistant", "content": "Digital Equity offers Coursera access."}]
        for question in ("How do I sign up?", "Register", "How do I register for that?"):
            with self.subTest(question=question):
                _, sources = server.retrieval_plan(question, {"url": server.ROOT_URL}, history)
                self.assertIn("page-opportunities-34b5847f", [s["id"] for s in sources])
                self.assertIn("Coursera", server.retrieval_prompt(question, sources, conversation_history=history))

    def test_new_york_clock_on_both_sides_of_dst(self):
        self.assertEqual(str(server.site_today(datetime(2026, 9, 10, 3, 30, tzinfo=timezone.utc))), "2026-09-09")
        self.assertEqual(str(server.site_today(datetime(2026, 1, 10, 4, 30, tzinfo=timezone.utc))), "2026-01-09")
        self.assertIn("CURRENT TIME (America/New_York; an ended session is not upcoming)", server.retrieval_prompt("upcoming", []))

    def test_full_month_and_explicit_past_date_keep_calendar_rows(self):
        source = {"id": "calendar", "calendar_events": [
            {"date": "2026-09-01", "label": "September 1 workshop"},
            {"date": "2026-09-15", "label": "September 15 workshop"},
        ]}
        today = server.date(2026, 9, 10)
        full = server.calendar_evidence_blocks(source, "Show the full calendar", today)
        self.assertIn("September 1 workshop", full)
        self.assertIn("September 15 workshop", full)
        past = server.calendar_evidence_blocks(source, "What was on September 1?", today)
        self.assertIn("September 1 workshop", past)
        self.assertNotIn("September 15 workshop", past)
        upcoming = server.calendar_evidence_blocks(source, "Upcoming workshops", today)
        self.assertNotIn("September 1 workshop", upcoming)

    def test_calendar_does_not_merge_old_snapshot_into_live_rows(self):
        source = {"id": "calendar", "calendar_source": "live_downloadable_calendar",
                  "calendar_schedule": {"month": "September 2026"},
                  "calendar_events": [{"date": "2026-09-15", "label": "September 15: current event"}],
                  "blocks": ["September 15: obsolete event"]}
        content = server.source_excerpt(source, "full calendar", today=server.date(2026, 9, 10))
        self.assertIn("current event", content)
        self.assertNotIn("obsolete event", content)

    def test_calendar_status_is_stale_after_ttl(self):
        cache = LiveCalendarCache(ttl_seconds=60, fetcher=lambda _: {"id": "calendar"})
        with patch("live_calendar.time.monotonic", return_value=100):
            cache.refresh({})
            self.assertEqual(cache.status()["status"], "live")
        with patch("live_calendar.time.monotonic", return_value=161):
            self.assertEqual(cache.status()["status"], "stale")

    def test_excerpt_preserves_section_order_not_keyword_score_order(self):
        source = {"id": "sample", "headings": ["Account access", "Volunteers"],
                  "blocks": ["Account access", "Contact the team", "Volunteers", "Apply here"]}
        excerpt = server.source_excerpt(source, "Account access and volunteers")
        self.assertLess(excerpt.index("Contact the team"), excerpt.index("Volunteers"))
        self.assertLess(excerpt.index("Volunteers"), excerpt.index("Apply here"))

    def test_gateway_uses_candidate_enum_and_one_request(self):
        prompt = server.retrieval_prompt("Courses", [server.SOURCE_BY_ID["page-opportunities-34b5847f"]])
        reply = {"model": "z-ai/glm-5.3-flash", "choices": [{
            "message": {"content": '{"pick":"page-opportunities-34b5847f","answer":"A source-backed answer."}'},
            "finish_reason": "stop"}], "usage": {}}
        with patch.object(server, "CAIL_KEY", "synthetic-key"), \
             patch.object(server.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(reply).encode())) as call:
            result = server.model_completion([{"role": "system", "content": prompt}])
        self.assertEqual(result["provider"], "cail")
        self.assertEqual(call.call_count, 1)
        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(payload["model"], "glm-5.3-flash")
        self.assertEqual(payload["reasoning"]["effort"], "low")
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        enum = payload["response_format"]["json_schema"]["schema"]["properties"]["pick"]["enum"]
        self.assertEqual(enum, ["ASK", "page-opportunities-34b5847f"])

    def test_gateway_failure_does_not_call_other_providers(self):
        with patch.object(server, "CAIL_KEY", "synthetic-key"), \
             patch.object(server, "cail_completion", side_effect=RuntimeError("offline")) as call, \
             patch.object(server, "ollama_completion") as legacy, \
             patch.object(server, "openrouter_completion") as fallback:
            with self.assertRaises(RuntimeError):
                server.model_completion([])
        self.assertEqual(call.call_count, 1)
        legacy.assert_not_called()
        fallback.assert_not_called()

    def test_failure_diagnostics_include_known_model_call_state(self):
        import inspect
        self.assertIn("t.model_called", inspect.getsource(EvaluationStore.get_conversation))


if __name__ == "__main__":
    unittest.main()
