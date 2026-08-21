import pathlib
import unittest

from scripts import audit_conversation_quality


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ConversationQualityAuditTests(unittest.TestCase):
    def test_audit_requires_an_explicit_database_url(self):
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL is required"):
            audit_conversation_quality.run_audit("")

    def test_audit_declares_the_content_free_boundary(self):
        source = (ROOT / "scripts" / "audit_conversation_quality.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"message_content_selected": False', source)
        self.assertNotIn("SELECT m.content", source)
        self.assertNotIn("SELECT content FROM conversation_messages", source)

    def test_audit_enforces_the_human_only_review_boundary(self):
        source = (ROOT / "scripts" / "audit_conversation_quality.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("client_surface NOT IN ('replica', 'wix')", source)
        self.assertIn("AS nonhuman_ready_turns", source)
        self.assertNotIn("AS nonsynthetic_ready_turns", source)

    def test_json_values_are_bounded_for_operator_output(self):
        self.assertEqual(
            audit_conversation_quality._json_value({"latency": 1.236}),
            {"latency": 1.24},
        )


if __name__ == "__main__":
    unittest.main()
