"""The evidence citation and the participant's next action are separate."""
import json
import unittest

import server


class ActionLinkTests(unittest.TestCase):
    def test_faq_excerpt_matches_terms_in_the_answer_not_just_the_question(self):
        for key in ("home", "contact"):
            excerpt = server.source_excerpt(server.SOURCE_BY_ID[key], "Do regular classes also accept walk-ins?")
            self.assertIn("we allow walk-in attendance", excerpt)
            self.assertIn("priority", excerpt)

    def test_registration_can_cite_contact_faq_but_link_to_calendar(self):
        sources = [server.SOURCE_BY_ID["contact"], server.SOURCE_BY_ID["calendar"]]
        reply = server.parse_model_selection(json.dumps({
            "pick": "contact", "answer": "Use the Register link on the calendar.",
            "action_url": server.CALENDAR_URL,
        }), "How do I register?", sources)
        self.assertEqual(reply["sources"][0]["id"], "contact")
        self.assertEqual(reply["action"]["url"], server.CALENDAR_URL)

    def test_actual_link_is_allowed_even_when_destination_is_not_a_candidate(self):
        source = {**server.SOURCE_BY_ID["contact"], "source_links": [
            {"label": "Class signup", "url": server.CALENDAR_URL},
            {"label": "Download schedule", "url": "https://static.example.org/schedule.pdf"},
        ]}
        for url in (server.CALENDAR_URL, "https://static.example.org/schedule.pdf"):
            self.assertEqual(server.model_action_link(url, [source])["url"], url)

    def test_unprovided_urls_and_unsafe_schemes_do_not_become_actions(self):
        source = {**server.SOURCE_BY_ID["contact"], "source_links": []}
        for url in (server.CALENDAR_URL, "https://evil.example/calendar", "javascript:alert(1)",
                    server.CONTACT_URL + "?invented=1", None, {}):
            self.assertIsNone(server.model_action_link(url, [source]))

    def test_invalid_optional_action_does_not_reject_valid_answer(self):
        for value in ("javascript:alert(1)", "https://evil.example/calendar", None):
            reply = server.parse_model_selection(json.dumps({
                "pick": "contact", "answer": "Walk-ins are allowed for regular classes.",
                "action_url": value,
            }), "Do I have to register?", [server.SOURCE_BY_ID["contact"]])
            self.assertEqual(reply["kind"], "answer")
            self.assertNotIn("action", reply)


if __name__ == "__main__":
    unittest.main()
