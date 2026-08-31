#!/usr/bin/env python3
"""Privacy and scope contracts for the nightly evaluation digest."""

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "daily_evaluation_digest.py"
SPEC = importlib.util.spec_from_file_location("daily_evaluation_digest", SCRIPT)
DIGEST = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(DIGEST)


class DailyEvaluationDigestTests(unittest.TestCase):
    def test_digest_never_reads_participant_message_text(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("conversation_messages", source)
        self.assertNotIn("DATABASE_URL\"", source.split("print(", 1)[-1])
        self.assertIn("decision_questions", source)
        self.assertIn("is_automated", source)

    def test_evaluator_text_receives_basic_redaction(self):
        cleaned = DIGEST.safe_text("Email person@example.org or call 212-555-1212")
        self.assertNotIn("person@example.org", cleaned)
        self.assertNotIn("212-555-1212", cleaned)
        self.assertIn("[redacted-email]", cleaned)
        self.assertIn("[redacted-number]", cleaned)

    def test_conversation_references_are_one_way_and_bounded(self):
        reference = DIGEST.conversation_ref("11111111-1111-4111-8111-111111111111")
        self.assertEqual(len(reference), 8)
        self.assertNotIn("11111111", reference)


if __name__ == "__main__":
    unittest.main(verbosity=2)
