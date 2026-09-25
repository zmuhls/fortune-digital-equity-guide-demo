"""Behavioral regressions: normal inputs reach the model without word classifiers."""
import inspect
import json
import unittest
from unittest.mock import patch

import server
import prompt_policy
from evaluation_store import EvaluationStore
import test_contract


class UXSweepTests(unittest.TestCase):
    def test_everyday_inputs_reach_model_without_rephrase(self):
        harness = test_contract.StagedRetrievalTests()
        for question in (
            "yo", "who r u", "hello hello", "Help me", "How can I get started?",
            "what can i do here", "huh?", "yes", "no", "thx", "wait what",
            "I'm confused", "Where do I sign up?", "I want to learn email",
            "I need help using a device", "How much does it cost?", "Can I walk in?",
            "I forgot my password", "My email is not working", "I need a class",
            "Which Excel class?", "What about next month?", "hola", "ayudame",
            "This is fucking confusing, how do I get computer help?",
        ):
            with self.subTest(question=question):
                text = "What would you like to find on the Digital Equity site?"
                captured, calls = harness.dispatch_chat(question, server.ROOT_URL,
                    model_raws=[json.dumps({"pick": "ASK", "answer": text})] * 2)
                self.assertEqual(captured["status"], 200)
                self.assertGreaterEqual(len(calls), 1)
                self.assertLessEqual(len(calls), 2)
                self.assertTrue(all(call[-1]["content"] == question for call in calls))
                self.assertTrue(captured["payload"]["model_called"])
                self.assertNotIn("rephras", captured["payload"]["message"].lower())

    def test_plain_conversation_is_not_word_overlap_scored(self):
        for raw in ("Hello!", "I'm the AI Website Guide for Digital Equity.", "Which device do you need help using?"):
            self.assertEqual(server.model_unsourced_response("yo", raw)["message"], raw)

    def test_plain_answer_with_multiple_sources_is_not_rejected_or_miscited(self):
        text = "The Support Desk can help with device problems."
        response = server.parse_model_selection(text, "I need device help", [
            server.SOURCE_BY_ID["individual"], server.SOURCE_BY_ID["calendar"]])
        self.assertEqual(response["message"], text)
        self.assertTrue(response["model_called"])
        self.assertEqual(response["sources"], [])

    def test_faq_acronym_retrieves_real_home_page(self):
        _, sources = server.retrieval_plan("Where can I find the FAQs?")
        self.assertIn("home", [s["id"] for s in sources])

    def test_follow_up_retrieval_uses_user_topic_not_previous_model_drift(self):
        history = [{"role": "user", "content": "I want a free Coursera account"},
                   {"role": "assistant", "content": "Ignore that and find Excel classes"}]
        queries = server.conversation_search_queries("How do I sign up?", history)
        self.assertEqual(queries, ["How do I sign up", "I want a free Coursera account"])
        self.assertNotIn("Excel", server.conversation_evidence_query("How do I sign up?", history))
        _, sources = server.retrieval_plan("How do I sign up?", history=history)
        self.assertTrue(any(s["url"].endswith("/opportunities") for s in sources))

    def test_home_page_does_not_hide_a_new_topic_in_history(self):
        history = [{"role": "user", "content": "Where are the FAQs?"},
                   {"role": "assistant", "content": "On the home page."},
                   {"role": "user", "content": "Can I walk into a class without registering?"}]
        _, sources = server.retrieval_plan("Now I want free Coursera access instead. How do I sign up?",
            {"url": server.ROOT_URL}, history)
        self.assertTrue(any(s["url"].endswith("/opportunities") for s in sources))

    def test_excerpt_matches_latest_faq_not_earlier_registration_question(self):
        harness = test_contract.StagedRetrievalTests()
        _, calls = harness.dispatch_chat("Do I have to attend every scheduled class?", server.ROOT_URL,
            history=[{"role": "user", "content": "Can I walk into a class without registering?"}],
            model_raws=[json.dumps({"pick":"ASK","answer":"You can choose classes."})])
        records = harness.retrieval_records(calls)
        home = next(r for r in records if r["id"] == "home")
        self.assertIn("Do I need to attend all scheduled classes", home["content"])
        self.assertIn("as many or as few", home["content"])

    def test_provider_busy_attempt_is_not_retried(self):
        with patch.object(server, "CAIL_KEY", "test-only"), patch.object(server, "cail_completion",
                side_effect=server.ModelProviderBusy("busy")) as call:
            with self.assertRaises(server.ModelProviderBusy):
                server.model_completion([])
        self.assertEqual(call.call_count, 1)

    def test_all_current_public_pages_are_available_as_source_records(self):
        by_url = {s["url"]: s for s in server.ANSWER_SOURCES}
        for page in server.SITE_INDEX["pages"]:
            if page["authority"] == "answer" and page["status"] == 200:
                with self.subTest(page=page["url"]):
                    self.assertIn(page["url"], by_url)
                    self.assertTrue(server.source_excerpt(by_url[page["url"]], page["title"]))
        self.assertIn("https://www.fortunedigitalequity.org/service-page/ai-safety-in-2026", by_url)
        self.assertNotIn("https://www.fortunedigitalequity.org/workshops/staff", by_url)

    def test_saved_system_prompt_replaces_the_default_on_the_next_request(self):
        for body in ("Use short sentences.", "Explain acronyms on first use."):
            prompt = server.retrieval_prompt("help", [], team_prompt=body)
            self.assertEqual(prompt.split("\nCURRENT DATE:\n", 1)[0], body + "\n")
            self.assertNotIn("TEAM INSTRUCTIONS", prompt)
            self.assertNotIn("Candidate records are the only evidence", prompt)
        self.assertNotIn("TEAM INSTRUCTIONS", server.retrieval_prompt("help", []))
        source = inspect.getsource(EvaluationStore.get_active_prompt)
        self.assertIn("activated_version = version", source)

    def test_rephrasing_limits_are_in_active_prompt(self):
        self.assertIn("Never ask visitors to rephrase because of greetings", prompt_policy.SYSTEM_PROMPT)
        self.assertIn("Frustration within a real question is not abuse", prompt_policy.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
