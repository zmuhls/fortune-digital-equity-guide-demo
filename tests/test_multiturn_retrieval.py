import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server
from scripts import run_website_guide_eval
from scripts import run_website_guide_multiturn_eval


HOME = {"url": "https://www.fortunedigitalequity.org/"}


class MultiTurnRetrievalTests(unittest.TestCase):
    def test_frozen_suite_has_episode_and_context_coverage(self):
        document = run_website_guide_eval.load_json(
            ROOT / "evals" / "website-guide" / "multiturn-cases.json"
        )
        self.assertEqual(run_website_guide_multiturn_eval.validate_suite(document), [])
        self.assertGreaterEqual(len(document["episodes"]), 10)
        turns = [turn for episode in document["episodes"] for turn in episode["turns"]]
        self.assertGreaterEqual(len(turns), 40)
        self.assertGreaterEqual(
            sum(turn.get("mode") in {"deictic", "elliptical", "topic_shift"} for turn in turns),
            10,
        )

    def test_elliptical_follow_up_uses_latest_safe_topic(self):
        history = [
            {"role": "user", "content": "Can I get a free laptop?"},
            {"role": "assistant", "content": "Laptop supply is limited and can take time."},
        ]
        queries = server.conversation_search_queries("Is that available now?", history)
        self.assertTrue(any("free laptop" in query for query in queries))
        scope, sources = server.retrieval_plan("Is that available now?", HOME, history)
        self.assertEqual(scope, "site")
        self.assertEqual(sources[0]["id"], "devices")

    def test_latest_topic_wins_after_a_topic_shift(self):
        history = [
            {"role": "user", "content": "Can I get a free laptop?"},
            {"role": "assistant", "content": "Laptop supply is limited and can take time."},
            {"role": "user", "content": "Instead, tell me about Intro to Canva."},
            {"role": "assistant", "content": "Intro to Canva covers design basics."},
        ]
        _, sources = server.retrieval_plan("What does that cover?", HOME, history)
        self.assertEqual(sources[0]["id"], server.INTRO_CANVA_ID)

    def test_explicit_topic_shift_is_not_rewritten_as_an_elliptical_follow_up(self):
        history = [
            {"role": "user", "content": "Is there a class about writing resumes with AI?"},
            {"role": "assistant", "content": "Resume Writing in an AI World."},
        ]
        question = "Is there also a class on job searching online?"
        _, sources = server.retrieval_plan(question, HOME, history)
        self.assertEqual(sources[0]["id"], server.JOB_SEARCH_ID)

    def test_generic_class_words_do_not_override_an_elliptical_follow_up(self):
        history = [
            {"role": "user", "content": "Which class teaches Excel formulas?"},
            {"role": "assistant", "content": "It begins with basic operators."},
        ]
        _, sources = server.retrieval_plan(
            "What else does that class cover?",
            HOME,
            history,
        )
        self.assertEqual(sources[0]["id"], server.EXCEL_FORMULAS_ID)

    def test_natural_generic_follow_ups_keep_the_latest_support_and_calendar_topics(self):
        support_history = [
            {"role": "user", "content": "What one-on-one technology help is available?"},
            {"role": "assistant", "content": "Fortune lists tutoring and technical support."},
        ]
        _, support_sources = server.retrieval_plan(
            "What kinds of help are offered?",
            HOME,
            support_history,
        )
        self.assertEqual(support_sources[0]["id"], "individual")

        calendar_history = [
            {"role": "user", "content": "What current schedule is shown on this page?"},
            {"role": "assistant", "content": "The calendar lists current sessions."},
        ]
        scope, calendar_sources = server.retrieval_plan(
            "What are the regular class hours?",
            {"url": server.CALENDAR_URL},
            calendar_history,
        )
        self.assertEqual(scope, "page")
        self.assertEqual(calendar_sources[0]["id"], "calendar")

    def test_explicit_catalog_and_schedule_topic_shifts_do_not_inherit_a_device_topic(self):
        history = [
            {"role": "user", "content": "Can I get a free laptop?"},
            {"role": "assistant", "content": "Laptop supply is limited."},
        ]

        catalog_question = "What kinds of classes are offered?"
        _, catalog_sources = server.retrieval_plan(catalog_question, HOME, history)
        self.assertEqual(catalog_sources[0]["id"], "trainings")

        schedule_question = "What are the regular class hours?"
        scope, schedule_sources = server.retrieval_plan(schedule_question, HOME, history)
        self.assertEqual(scope, "site")
        self.assertEqual(schedule_sources[0]["id"], "calendar")

    def test_conversational_it_does_not_mean_the_host_page(self):
        self.assertFalse(server.question_refers_to_current_page("What does it cover?"))
        self.assertTrue(server.question_refers_to_current_page("What does this page cover?"))

    def test_specific_class_questions_prefer_specific_pages(self):
        expected = {
            "I barely know how to use a computer. Is there a beginner class?": server.INTRO_COMPUTERS_ID,
            "I want to learn email from the beginning. What class fits?": server.INTRO_EMAIL_ID,
            "Does Fortune have a beginner Canva class?": server.INTRO_CANVA_ID,
            "Is there a class for learning a new smartphone?": server.INTRO_SMARTPHONE_ID,
            "Now I want to learn Excel formulas.": server.EXCEL_FORMULAS_ID,
            "Is there also a class on job searching online?": server.JOB_SEARCH_ID,
            "Busco una clase básica de computación.": server.SPANISH_BASIC_ID,
        }
        for question, source_id in expected.items():
            with self.subTest(question=question):
                _, sources = server.retrieval_plan(question, HOME)
                self.assertEqual(sources[0]["id"], source_id)

    def test_removed_resume_class_uses_current_discovery_sources_without_guessing(self):
        question = "Is there a class about writing resumes with AI?"
        scope, sources = server.retrieval_plan(question, HOME)
        self.assertEqual(scope, "site")
        self.assertEqual([source["id"] for source in sources], ["trainings", "contact"])

    def test_word_certification_follow_up_prefers_word_certification_page(self):
        history = [
            {"role": "user", "content": "Which certifications are listed?"},
            {"role": "assistant", "content": "Microsoft Office certifications are listed."},
        ]
        _, sources = server.retrieval_plan("Is there one for Word?", HOME, history)
        self.assertEqual(sources[0]["id"], server.WORD_CERTIFICATION_ID)

    def test_excel_topic_survives_five_short_follow_ups_without_a_classifier(self):
        history = [
            {"role": "user", "content": "Which Excel class teaches formulas and functions?"},
            {"role": "assistant", "content": "Excel - Formulas & Functions covers basic operators."},
            {"role": "user", "content": "What else?"},
            {"role": "assistant", "content": "It also covers built-in functions."},
            {"role": "user", "content": "And after that?"},
            {"role": "assistant", "content": "The page lists additional spreadsheet topics."},
            {"role": "user", "content": "Can you be more specific?"},
            {"role": "assistant", "content": "The approved page has more detail."},
            {"role": "user", "content": "What about the next part?"},
            {"role": "assistant", "content": "The same class page remains the source."},
        ]
        _, sources = server.retrieval_plan("What would I learn next?", HOME, history)
        self.assertEqual(sources[0]["id"], server.EXCEL_FORMULAS_ID)
        self.assertIn(
            "Which Excel class teaches formulas and functions",
            server.conversation_evidence_query("What would I learn next?", history),
        )

    def test_ambiguous_specific_sources_still_reach_the_model_selector(self):
        question = "Which Excel class covers formatting and organizing data?"
        scope, sources = server.retrieval_plan(question, HOME)
        self.assertEqual(scope, "site")
        self.assertEqual(
            {source["id"] for source in sources[:2]},
            {
                server.source_id_for_path("/service-page/excel-formatting-data"),
                server.source_id_for_path("/service-page/excel-organizing-data"),
            },
        )

    def test_follow_up_evidence_uses_the_selected_source_title(self):
        source = server.SOURCE_BY_ID[server.INTRO_CANVA_ID]
        evidence = server.source_excerpt(
            source,
            "Does Fortune have a beginner Canva class? What would I learn there?",
            limit=server.MAX_MODEL_EXCERPT_CHARS,
        )
        self.assertTrue(
            {"canva", "template", "design", "interface"}.intersection(
                server.tokens(evidence, keep_stopwords=True)
            )
        )

    def test_email_curriculum_follow_up_advances_beyond_the_opening_summary(self):
        source = server.SOURCE_BY_ID[server.INTRO_EMAIL_ID]
        evidence = server.source_excerpt(
            source,
            (
                "I want to learn email from the beginning. What class fits? "
                "What would I learn there?"
            ),
            limit=server.MAX_MODEL_EXCERPT_CHARS,
        )
        self.assertIn("creating or accessing an email account", evidence)
        self.assertIn("adding and opening attachments", evidence)

    def test_follow_up_excludes_evidence_used_anywhere_in_recent_history(self):
        source = server.SOURCE_BY_ID["individual"]
        prior = (
            "One-to-one tutoring is available online or in person by appointment. "
            "Technical support is listed at Long Island City Tuesday and Thursday."
        )
        evidence = server.grounded_evidence_sentences(
            source,
            "Can staff repair a broken phone?",
            require_overlap=True,
            focus_query="Can staff repair a broken phone?",
            prior_answer=prior,
        )
        self.assertNotIn(
            "One-to-one tutoring is available online or in person by appointment.",
            evidence,
        )
        self.assertNotIn(
            "Technical support is listed at Long Island City Tuesday and Thursday.",
            evidence,
        )

    def test_schedule_follow_up_prefers_live_calendar_evidence(self):
        source = server.SOURCE_BY_ID["calendar"]
        evidence = server.source_excerpt(
            source,
            "Intro to Smartphones. Follow-up: When is it offered?",
            limit=server.MAX_MODEL_EXCERPT_CHARS,
        )
        self.assertIn("available classes", evidence.lower())

    def test_advancement_grader_rejects_a_reused_source_sentence(self):
        history = [
            {"role": "user", "content": "Can I get support?"},
            {
                "role": "assistant",
                "content": "One-to-one tutoring is available online or in person by appointment.",
            },
        ]
        response = {
            "kind": "answer",
            "message": "One-to-one tutoring is available online or in person by appointment.",
        }
        self.assertEqual(
            run_website_guide_multiturn_eval.advancement_failures(
                response=response,
                history=history,
            ),
            ["continuity: answer repeats prior evidence instead of advancing"],
        )

    def test_advancement_grader_accepts_a_repeated_caveat_with_new_evidence(self):
        caveat = "This service is currently not available, so contact for more information."
        history = [
            {"role": "user", "content": "What does the introductory class cover?"},
            {
                "role": "assistant",
                "content": (
                    f"{caveat} The introductory class covers entering, editing, "
                    "selecting, moving, and copying information."
                ),
            },
        ]
        response = {
            "kind": "answer",
            "message": (
                f"{caveat} The formatting class covers titles, numbers, dates, "
                "currency, borders, and cell styles."
            ),
        }
        self.assertEqual(
            run_website_guide_multiturn_eval.advancement_failures(
                response=response,
                history=history,
            ),
            [],
        )

    def test_advancement_grader_accepts_new_evidence_in_an_expanded_sentence(self):
        history = [
            {"role": "user", "content": "What does the formatting class cover?"},
            {
                "role": "assistant",
                "content": (
                    "It covers titles, headers, text, numbers, dates, currency, "
                    "percentages, alignment, borders, cell styles, and the format painter."
                ),
            },
        ]
        response = {
            "kind": "answer",
            "message": (
                "It covers titles, headers, text, numbers, dates, times, currency, "
                "and percentages, plus alignment, wrapping, merging, fills, borders, "
                "cell styles, the format painter, paste options, resetting formatting, "
                "and automatic highlighting of important values."
            ),
        }
        self.assertEqual(
            run_website_guide_multiturn_eval.advancement_failures(
                response=response,
                history=history,
            ),
            [],
        )

    def test_advancement_grader_rejects_a_compressed_subset(self):
        history = [
            {"role": "user", "content": "What does the formatting class cover?"},
            {
                "role": "assistant",
                "content": (
                    "The class covers titles, headers, text, numbers, dates, currency, "
                    "percentages, alignment, borders, cell styles, and the format painter."
                ),
            },
        ]
        response = {
            "kind": "answer",
            "message": "It covers dates, currency, alignment, borders, and cell styles.",
        }
        self.assertEqual(
            run_website_guide_multiturn_eval.advancement_failures(
                response=response,
                history=history,
            ),
            ["continuity: answer repeats prior evidence instead of advancing"],
        )

    def test_advancement_grader_allows_requested_prior_qualification_detail(self):
        history = [
            {"role": "user", "content": "Can I get a refurbished laptop?"},
            {
                "role": "assistant",
                "content": (
                    "You must be an active or previous attendee of at least five "
                    "Digital Equity workshops to qualify for a refurbished laptop."
                ),
            },
        ]
        response = {
            "kind": "answer",
            "message": (
                "You must be an active or previous attendee of at least five "
                "Digital Equity workshops to qualify."
            ),
        }
        self.assertEqual(
            run_website_guide_multiturn_eval.advancement_failures(
                response=response,
                history=history,
                question="How would I qualify for that?",
            ),
            [],
        )

    def test_advancement_grader_allows_requested_prior_class_detail(self):
        prior = "The class covers formatting titles, alignment, wrapping, and borders."
        history = [
            {"role": "user", "content": "Tell me about the formatting class."},
            {"role": "assistant", "content": prior},
        ]
        self.assertEqual(
            run_website_guide_multiturn_eval.advancement_failures(
                response={"kind": "answer", "message": prior},
                history=history,
                question="What formatting techniques does that class cover?",
            ),
            [],
        )

    def test_advancement_grader_does_not_exempt_open_ended_follow_ups(self):
        prior = "One-to-one tutoring is available online or in person by appointment."
        history = [
            {"role": "user", "content": "Can I get support?"},
            {"role": "assistant", "content": prior},
        ]
        for question in ("What else?", "Tell me more.", "What next?"):
            with self.subTest(question=question):
                self.assertEqual(
                    run_website_guide_multiturn_eval.advancement_failures(
                        response={"kind": "answer", "message": prior},
                        history=history,
                        question=question,
                    ),
                    ["continuity: answer repeats prior evidence instead of advancing"],
                )

    def test_advancement_grader_requires_the_requested_detail_in_prior_answer(self):
        prior = "Refurbished laptop supply is limited, and there may be a wait."
        history = [
            {"role": "user", "content": "Can I get a refurbished laptop?"},
            {"role": "assistant", "content": prior},
        ]
        self.assertEqual(
            run_website_guide_multiturn_eval.advancement_failures(
                response={"kind": "answer", "message": prior},
                history=history,
                question="How would I qualify for that?",
            ),
            ["continuity: answer repeats prior evidence instead of advancing"],
        )

    def test_advancement_grader_rejects_a_paraphrase_without_new_evidence(self):
        history = [
            {"role": "user", "content": "Can I get support?"},
            {
                "role": "assistant",
                "content": "One-to-one tutoring is available online or in person by appointment.",
            },
        ]
        response = {
            "kind": "answer",
            "message": "Tutoring is available by appointment, either online or in person.",
        }
        self.assertEqual(
            run_website_guide_multiturn_eval.advancement_failures(
                response=response,
                history=history,
            ),
            ["continuity: answer repeats prior evidence instead of advancing"],
        )

    def test_evidence_cleanup_removes_form_and_image_scaffolding(self):
        fragments = [
            "QRCode for Pre-Computer Safety Survey",
            "Your content has been submitted",
            "Ended Ended Main Office (LIC)",
            "Computer Lab Clip Art",
            "IMG_0210_edited.jpg",
        ]
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                self.assertEqual(server.clean_evidence_fragment(fragment), "")

    def test_possessive_source_names_match_plain_query_terms(self):
        self.assertIn("canva", server.tokens("Canva's interface"))

    def test_every_active_source_keeps_reachable_grounded_evidence(self):
        missing = []
        for source in server.RETRIEVABLE_SOURCES:
            evidence = server.grounded_evidence_sentences(
                source,
                server.clean_source_title(source),
                require_overlap=False,
            )
            if not evidence:
                missing.append(source["id"])
        self.assertEqual(missing, [])

    def test_device_eligibility_follow_up_advances_past_availability(self):
        source = server.SOURCE_BY_ID["devices"]
        evidence = server.source_excerpt(
            source,
            "Can I get a free laptop? How do I confirm whether I qualify?",
            limit=server.MAX_MODEL_EXCERPT_CHARS,
        )
        self.assertIn("at least 5", evidence)
        self.assertIn("workshops", evidence)
        self.assertNotIn("currently on hold", evidence)

    def test_continuity_grader_accepts_stable_follow_up(self):
        response = {"chat_stage": "follow_up", "conversation_id": "same"}
        history = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]
        self.assertEqual(
            run_website_guide_multiturn_eval.continuity_failures(
                turn_index=1,
                response=response,
                conversation_id="same",
                history=history,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
