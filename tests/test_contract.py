#!/usr/bin/env python3
"""Key-free contract tests for the context-aware Digital Equity guide."""

import io
import json
import pathlib
import sys
import unittest


DEMO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

import server


class SiteIndexTests(unittest.TestCase):
    def test_complete_public_sitemap_inventory_is_present(self):
        self.assertTrue(server.SITE_INDEX_PATH.exists())
        self.assertEqual(server.SITE_INDEX["unique_urls"], 184)
        self.assertEqual(server.SITE_INDEX["sitemap_entries"], 185)
        self.assertEqual(len(server.SITE_INDEX["pages"]), 184)

    def test_authority_boundary_is_explicit(self):
        self.assertEqual(
            server.SITE_INDEX["authority_counts"],
            {"answer": 147, "excluded": 17, "archive": 13, "navigation": 7},
        )
        self.assertGreaterEqual(len(server.ANSWER_SOURCES), 140)
        self.assertTrue(all(source["authority"] == "answer" for source in server.ANSWER_SOURCES))

    def test_every_indexed_url_stays_on_the_public_digital_equity_host(self):
        for page in server.SITE_INDEX["pages"]:
            self.assertTrue(page["url"].startswith("https://www.fortunedigitalequity.org/"), page["url"])
            for link in page["internal_links"]:
                self.assertTrue(link.startswith("https://www.fortunedigitalequity.org/"), link)

    def test_archives_tests_and_member_surfaces_cannot_support_answers(self):
        prohibited_fragments = ("/post/", "/test", "/members", "/groups", "/file-share", "archive")
        for source in server.ANSWER_SOURCES:
            self.assertFalse(any(fragment in source["url"] for fragment in prohibited_fragments), source["url"])

    def test_reviewed_core_sources_remain_available(self):
        self.assertTrue({"home", "trainings", "devices", "individual", "calendar", "contact"}.issubset(server.SOURCE_BY_ID))
        for source_id in ("home", "trainings", "devices", "individual", "calendar", "contact"):
            self.assertTrue(server.SOURCE_BY_ID[source_id]["facts"])

    def test_internal_drive_material_is_not_a_public_model_source(self):
        self.assertNotIn("docs.google.com", server.BASE_SYSTEM_PROMPT)
        self.assertNotIn("drive.google.com", server.BASE_SYSTEM_PROMPT)
        self.assertFalse(any("docs.google.com" in page["url"] for page in server.SITE_INDEX["pages"]))


class RetrievalTests(unittest.TestCase):
    def test_retrieval_finds_specific_booking_services(self):
        robot = server.retrieve_sources("robot coding")
        spanish = server.retrieve_sources("Spanish digital literacy")
        excel = server.retrieve_sources("Excel pivot table")
        self.assertIn("robot-coders-101", robot[0]["url"])
        self.assertIn("alfabetizaci", spanish[0]["url"])
        self.assertTrue(any("pivot-tables" in source["url"] for source in excel[:2]))

    def test_retrieval_keeps_device_question_on_device_route(self):
        self.assertEqual(server.retrieve_sources("Can I get a free laptop?")[0]["id"], "devices")

    def test_retrieval_never_returns_non_answer_authority(self):
        for query in ("2022 Tech Fair", "old blog post", "sample class", "member files"):
            for source in server.retrieve_sources(query):
                self.assertEqual(source["authority"], "answer")

    def test_page_context_is_canonicalized_and_weighted(self):
        context = server.sanitize_page_context({
            "url": "https://www.fortunedigitalequity.org/trainings?x=1#top",
            "path": "trainings",
            "title": "Workshops",
        })
        contextual = server.contextualize_sources(server.retrieve_sources("What else is here?"), context)
        self.assertEqual(context["url"], "https://www.fortunedigitalequity.org/trainings")
        self.assertEqual(contextual[0]["id"], "trainings")

    def test_external_page_context_is_not_trusted(self):
        context = server.sanitize_page_context({"url": "https://example.com/fake", "title": "Fake"})
        self.assertEqual(context["url"], "")


class StagedRetrievalTests(unittest.TestCase):
    def dispatch_chat(self, question, page_url, model_source_id="devices"):
        model_calls = []
        body = json.dumps({
            "message": question,
            "page_context": {"url": page_url, "title": "Current page"},
        }).encode()
        handler = server.Handler.__new__(server.Handler)
        handler.path = "/api/chat"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        captured = {}
        handler._json = lambda status, value: captured.update(status=status, payload=value)

        def record_model_call(_handler, messages):
            model_calls.append(messages)
            return json.dumps({
                "kind": "answer",
                "message": "Use the approved page.",
                "reason": "It contains the matching public information.",
                "source_ids": [model_source_id],
            })

        handler._ollama = record_model_call.__get__(handler, server.Handler)
        original_key = server.KEY
        server.KEY = "test-only-placeholder"
        try:
            handler.do_POST()
        finally:
            server.KEY = original_key
        return captured, model_calls

    @staticmethod
    def retrieval_records(model_calls):
        system_prompt = model_calls[0][0]["content"]
        marker = "\nAPPROVED RETRIEVAL RECORDS:\n"
        return json.loads(system_prompt.split(marker, 1)[1])

    def test_current_page_evidence_is_the_only_record_sent_to_model(self):
        captured, model_calls = self.dispatch_chat(
            "Can I get a free laptop?",
            "https://www.fortunedigitalequity.org/devices",
        )
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["retrieval_scope"], "page")
        self.assertEqual([record["id"] for record in self.retrieval_records(model_calls)], ["devices"])

    def test_every_content_complete_answer_url_resolves_to_page_only_evidence(self):
        complete_pages = [
            page for page in server.SITE_INDEX["pages"]
            if page.get("authority") == "answer" and page.get("status") == 200
        ]
        self.assertEqual(len(complete_pages), 144)
        for page in complete_pages:
            question = f"What does this page say about {page.get('title') or page['id']}?"
            with self.subTest(url=page["url"]):
                scope, sources = server.retrieval_plan(question, {
                    "url": page["url"],
                    "title": page.get("title", ""),
                })
                self.assertEqual(scope, "page")
                self.assertEqual([source["url"] for source in sources], [page["url"]])

    def test_non_answer_and_partial_urls_never_become_page_evidence(self):
        blocked_pages = [
            page for page in server.SITE_INDEX["pages"]
            if page.get("authority") != "answer" or page.get("status") != 200
        ]
        self.assertEqual(len(blocked_pages), 40)
        self.assertEqual(
            {page.get("authority") for page in blocked_pages},
            {"answer", "archive", "excluded", "navigation"},
        )
        for page in blocked_pages:
            question = f"What does this page say about {page.get('title') or page['id']}?"
            context = {"url": page["url"], "title": page.get("title", "")}
            with self.subTest(url=page["url"], authority=page.get("authority"), status=page.get("status")):
                self.assertIsNone(server.approved_current_page_source(context))
                scope, sources = server.retrieval_plan(question, context)
                self.assertNotEqual(scope, "page")
                self.assertNotIn(page["url"], [source["url"] for source in sources])

    def test_model_grounding_excerpts_come_only_from_the_validated_page_record(self):
        for source in server.ANSWER_SOURCES:
            question = f"What does this page say about {source.get('title') or source['id']}?"
            context = {"url": source["url"], "title": source.get("title", "")}
            with self.subTest(url=source["url"]):
                scope, sources = server.retrieval_plan(question, context)
                self.assertEqual(scope, "page")
                prompt = server.retrieval_prompt(question, sources, context)
                records = json.loads(prompt.split("\nAPPROVED RETRIEVAL RECORDS:\n", 1)[1])
                self.assertEqual([record["id"] for record in records], [source["id"]])
                self.assertEqual(records[0]["content"], server.source_excerpt(source, question))
                for grounded_line in records[0]["content"].splitlines():
                    self.assertIn(grounded_line, server.searchable_text(source))

    def test_site_search_occurs_only_after_current_page_miss(self):
        captured, model_calls = self.dispatch_chat(
            "Can I get a free laptop?",
            "https://www.fortunedigitalequity.org/trainings",
        )
        records = self.retrieval_records(model_calls)
        self.assertEqual(captured["payload"]["retrieval_scope"], "site")
        self.assertEqual([record["id"] for record in records], ["devices"])
        self.assertNotIn("trainings", [record["id"] for record in records])

    def test_page_reference_uses_only_the_current_page(self):
        captured, model_calls = self.dispatch_chat(
            "What does this page say?",
            "https://www.fortunedigitalequity.org/trainings",
            model_source_id="trainings",
        )
        self.assertEqual(captured["payload"]["retrieval_scope"], "page")
        self.assertEqual(
            [record["id"] for record in self.retrieval_records(model_calls)],
            ["trainings"],
        )

    def test_no_evidence_uses_staff_route_without_calling_model(self):
        captured, model_calls = self.dispatch_chat(
            "What is the zzyzx quasar permit policy?",
            "https://www.fortunedigitalequity.org/trainings",
        )
        payload = captured["payload"]
        self.assertEqual(payload["retrieval_scope"], "staff")
        self.assertEqual(payload["kind"], "handoff")
        self.assertFalse(payload["model_called"])
        self.assertEqual(model_calls, [])
        self.assertIn("could not find", payload["message"].lower())
        self.assertNotIn("Use the approved page.", payload["message"])
        self.assertEqual([source["id"] for source in payload["sources"]], ["contact"])

    def test_unknown_query_has_no_default_core_evidence(self):
        self.assertEqual(server.retrieve_sources("zzyzx quasar permit policy"), [])
        self.assertEqual(
            server.retrieval_plan(
                "zzyzx quasar permit policy",
                {"url": "https://www.fortunedigitalequity.org/trainings"},
            ),
            ("staff", []),
        )


class AmbiguityAndPrivacyTests(unittest.TestCase):
    def test_known_ambiguous_requests_ask_one_question_with_choices(self):
        for question in ("help", "device", "class", "internet"):
            response = server.ambiguity_response(question)
            self.assertIsNotNone(response, question)
            self.assertEqual(response["kind"], "clarify")
            self.assertEqual(response["message"].count("?"), 1)
            self.assertIn(len(response["choices"]), (2, 3))
            self.assertFalse(response["model_called"])
            self.assertTrue(response["related"])
            self.assertTrue(response["continuation"]["label"])

    def test_clear_requests_skip_deterministic_clarification(self):
        for question in ("Can I get a free laptop?", "I want an Excel pivot table class", "When is the email class?"):
            self.assertIsNone(server.ambiguity_response(question), question)

    def test_personal_details_are_held_before_model_use(self):
        cases = [
            "My Fortune ID is 12345",
            "My case number is ABC-9",
            "Email me at demo@example.com",
            "My date of birth is January 2",
            "My address is 100 Example Street",
        ]
        for text in cases:
            self.assertTrue(server.contains_personal_details(text), text)
        response = server.privacy_response("My Fortune ID is 12345")
        self.assertFalse(response["model_called"])
        self.assertNotIn("12345", response["message"])
        self.assertTrue(response["related"])

    def test_bare_six_digit_fortune_id_is_treated_as_personal_information(self):
        for text in (
            "123456",
            "123456 please",
            "Fortune 123456",
            "My number is 123456.",
            "123-456",
            "123 456",
            "１２３４５６",
            "١٢٣٤٥٦",
        ):
            self.assertTrue(server.contains_personal_details(text), text)
        self.assertFalse(server.contains_personal_details("12345"))
        self.assertFalse(server.contains_personal_details("1234567"))

    def test_six_digit_id_is_blocked_before_the_model_handler(self):
        model_calls = []
        original_key = server.KEY

        def record_model_call(handler, messages):
            model_calls.append(messages)
            raise AssertionError("The model must not receive a six-digit Fortune ID")

        body = json.dumps({"message": "123456"}).encode()
        handler = server.Handler.__new__(server.Handler)
        handler.path = "/api/chat"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler._ollama = record_model_call.__get__(handler, server.Handler)
        captured = {}
        handler._json = lambda status, value: captured.update(status=status, payload=value)

        server.KEY = "test-only-placeholder"
        try:
            handler.do_POST()
        finally:
            server.KEY = original_key

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["kind"], "privacy")
        self.assertFalse(captured["payload"]["model_called"])
        self.assertEqual(model_calls, [])

    def test_privacy_copy_names_pii_and_six_digit_fortune_id(self):
        message = server.privacy_response("123456")["message"]
        for phrase in (
            "personally identifiable information (PII)",
            "six-digit Fortune ID",
            "name",
            "contact information",
            "case information",
            "health information",
        ):
            self.assertIn(phrase, message)
        self.assertNotIn("123456", message)

    def test_normal_public_questions_pass_privacy_gate(self):
        for text in ("Where can I learn email?", "Can I get a free laptop?", "Where is the Long Island City class?"):
            self.assertFalse(server.contains_personal_details(text), text)

    def test_sensitive_or_case_specific_requests_use_pre_model_handoff(self):
        for text in ("I need parole advice", "Can you help with my health benefits?", "This is an emergency"):
            self.assertTrue(server.needs_human_handoff(text), text)
            response = server.human_handoff_response(text)
            self.assertEqual(response["kind"], "handoff")
            self.assertFalse(response["model_called"])
            self.assertEqual(response["handoff_url"], server.CONTACT_URL)


class ResponseContractTests(unittest.TestCase):
    def test_every_answer_has_source_related_route_handoff_and_continuation(self):
        retrieved = server.retrieve_sources("free laptop")
        raw = json.dumps({
            "kind": "answer",
            "message": "Review the device page and ask staff to confirm current criteria.",
            "reason": "Eligibility and inventory can change.",
            "source_ids": [retrieved[0]["id"]],
        })
        result = server.parse_model_json(raw, "free laptop", retrieved)
        self.assertTrue(result["sources"])
        self.assertTrue(result["related"])
        self.assertEqual(result["handoff_url"], server.CONTACT_URL)
        self.assertEqual(result["continuation"]["label"], "Ask the live guide")

    def test_unknown_model_source_ids_never_become_links(self):
        retrieved = server.retrieve_sources("free laptop")
        raw = '{"kind":"answer","message":"Use this.","reason":"It fits.","source_ids":["invented"]}'
        result = server.parse_model_json(raw, "free laptop", retrieved)
        self.assertNotIn("invented", [source["id"] for source in result["sources"]])
        self.assertEqual(result["sources"][0]["id"], retrieved[0]["id"])

    def test_model_prose_cannot_become_an_unsupported_factual_claim(self):
        retrieved = server.retrieve_sources("free laptop")
        raw = json.dumps({
            "kind": "answer",
            "message": "Free laptops are definitely available today with no wait.",
            "reason": "I know this from elsewhere.",
            "source_ids": ["devices"],
        })
        result = server.parse_model_json(raw, "free laptop", retrieved, "page")
        self.assertNotIn("definitely available today", result["message"])
        self.assertNotIn("I know this from elsewhere", result["reason"])
        self.assertIn("Laptop supply is limited", result["message"])
        self.assertIn("Distribution is currently on hold", result["message"])

    def test_model_clarification_cannot_restate_facts_or_reopen_a_clear_request(self):
        retrieved = server.retrieve_sources("free laptop")
        raw = json.dumps({
            "kind": "clarify",
            "message": "These are definitely the current qualifying rules. Are you eligible?",
            "reason": "Trust the model.",
            "source_ids": ["devices"],
        })
        result = server.parse_model_json(raw, "Can I get a free laptop?", retrieved, "page")
        self.assertEqual(result["kind"], "answer")
        self.assertNotIn("definitely", result["message"])
        self.assertIn("Distribution is currently on hold", result["message"])

    def test_malformed_model_output_falls_back_to_retrieved_sources(self):
        retrieved = server.retrieve_sources("free laptop")
        result = server.parse_model_json("Please check the device page.", "free laptop", retrieved)
        self.assertEqual(result["kind"], "answer")
        self.assertEqual(result["sources"][0]["id"], "devices")

    def test_answer_length_is_capped(self):
        retrieved = server.retrieve_sources("computer class")
        raw = '{"kind":"answer","message":"' + "word " * 120 + '","reason":"' + "why " * 50 + '","source_ids":["trainings"]}'
        result = server.parse_model_json(raw, "computer class", retrieved)
        self.assertLessEqual(len(result["message"].split()), server.MAX_MESSAGE_WORDS)
        self.assertLessEqual(len(result["reason"].split()), server.MAX_REASON_WORDS)

    def test_grounded_answers_use_short_source_extracts(self):
        for question in (
            "What does the program offer?",
            "Can I get a free laptop?",
            "When is the next Excel class?",
        ):
            retrieved = server.retrieve_sources(question)
            raw = json.dumps({
                "kind": "answer",
                "message": "Model prose is not shown.",
                "reason": "Use the sources.",
                "source_ids": [source["id"] for source in retrieved[:2]],
            })
            result = server.parse_model_json(raw, question, retrieved)
            self.assertLessEqual(len(result["message"].split()), server.MAX_MESSAGE_WORDS)

    def test_long_answers_prefer_a_complete_sentence_boundary(self):
        text = ("A useful first sentence has enough words to carry a complete participant-facing instruction clearly. "
                + "Extra material " * 100)
        clipped = server.clip_words(text, 30)
        self.assertTrue(clipped.endswith("clearly."))

    def test_related_routes_use_only_trusted_urls(self):
        for query in ("class", "laptop", "tutoring", "practice", "something else"):
            related = server.related_links(query, server.retrieve_sources(query))
            self.assertTrue(related)
            self.assertTrue(all(server.canonical_url(item["url"]) for item in related))

    def test_em_dash_normalization_does_not_leave_space_before_comma(self):
        self.assertEqual(server.clip_words("Great — a good starting point.", 20), "Great, a good starting point.")
        self.assertEqual(server.clip_words("Already spaced , badly.", 20), "Already spaced, badly.")

    def test_reasoning_tags_are_removed(self):
        self.assertEqual(server.strip_reasoning("secret plan</think>{\"kind\":\"answer\"}"), '{"kind":"answer"}')
        self.assertEqual(server.strip_reasoning("<think>secret</think>Visible"), "Visible")

    def test_history_drops_personal_details(self):
        safe_history = [
            {"role": "user", "content": "Where are classes?"},
            {"role": "assistant", "content": "Use the calendar."},
        ]
        private_values = (
            "123456",
            "123-456",
            "123 456",
            "１２３４５６",
            "١٢٣٤٥٦",
            "Email me at demo@example.com",
            "My SSN is 123-45-6789",
            "My case number is ABC-9",
        )
        for value in private_values:
            with self.subTest(value=value):
                history = safe_history + [{"role": "user", "content": value}]
                self.assertEqual(server.sanitize_history(history), safe_history)


class FrontendAndDeploymentTests(unittest.TestCase):
    def test_browser_origin_policy_allows_same_origin_and_rejects_unknown_origins(self):
        self.assertTrue(server.origin_is_allowed("", "127.0.0.1:8790"))
        self.assertTrue(server.origin_is_allowed("http://127.0.0.1:8790", "127.0.0.1:8790"))
        self.assertFalse(server.origin_is_allowed("https://unapproved.example", "127.0.0.1:8790"))

    def test_model_budget_enforces_hourly_and_shared_daily_limits(self):
        now = [1_000_000.0]
        budget = server.ModelCallBudget(2, 3, clock=lambda: now[0])
        self.assertTrue(budget.claim("client-a"))
        self.assertTrue(budget.claim("client-a"))
        self.assertFalse(budget.claim("client-a"))
        self.assertTrue(budget.claim("client-b"))
        self.assertFalse(budget.claim("client-c"))
        now[0] += 86400
        self.assertTrue(budget.claim("client-a"))

    def test_model_warmup_loads_once_per_cooldown(self):
        now = [100.0]
        warmer = server.ModelWarmup(60, clock=lambda: now[0])
        calls = []
        self.assertTrue(warmer.ensure(lambda: calls.append("load")))
        self.assertFalse(warmer.ensure(lambda: calls.append("load")))
        self.assertEqual(calls, ["load"])
        self.assertEqual(warmer.status(), "ready")
        now[0] += 61
        self.assertTrue(warmer.ensure(lambda: calls.append("load")))
        self.assertEqual(calls, ["load", "load"])
        now[0] += 59
        warmer.mark_ready()
        now[0] += 59
        self.assertFalse(warmer.ensure(lambda: calls.append("load")))

    def test_preload_uses_an_empty_request_and_keep_alive(self):
        payloads = []
        original_request = server.ollama_request
        server.ollama_request = lambda payload: payloads.append(payload) or {}
        try:
            server.preload_model()
        finally:
            server.ollama_request = original_request
        self.assertEqual(payloads, [{
            "model": server.MODEL,
            "stream": False,
            "keep_alive": server.MODEL_KEEP_ALIVE,
        }])

    def test_warmup_endpoint_requires_an_allowed_origin(self):
        handler = server.Handler.__new__(server.Handler)
        handler.path = "/api/warmup"
        handler.headers = {
            "Origin": "https://unapproved.example",
            "Host": "127.0.0.1:8790",
        }
        captured = {}
        handler._json = lambda status, value: captured.update(status=status, payload=value)
        handler.do_POST()
        self.assertEqual(captured["status"], 403)

    def test_health_and_public_runtime_never_expose_the_provider_key(self):
        server_source = (DEMO / "server.py").read_text(encoding="utf-8")
        config_source = (DEMO / "config.js").read_text(encoding="utf-8")
        self.assertNotIn('"OLLAMA_API_KEY": KEY', server_source)
        self.assertNotIn("'OLLAMA_API_KEY': KEY", server_source)
        self.assertNotIn("OLLAMA_API_KEY", config_source)
        self.assertIn('"model_enabled": bool(KEY)', server_source)

    def test_chat_only_panel_keeps_the_question_form_and_privacy_warning(self):
        html = (DEMO / "index.html").read_text(encoding="utf-8")
        app = (DEMO / "app.js").read_text(encoding="utf-8")
        panel = html[html.index('id="guide-panel"') : html.index("<!-- ROUTE_CONFIG -->")]
        self.assertIn('id="question-form"', panel)
        self.assertIn("Ask about this page", panel)
        self.assertIn("personally identifiable information (PII)", panel)
        self.assertNotIn("FAQ", panel)
        self.assertNotIn("FAQS", app)
        self.assertNotIn("renderMenu", app)
        self.assertNotIn("renderClasses", app)
        self.assertNotIn("questionField.focus", app)

    def test_guide_starts_compact_and_expands_to_reveal_the_answer(self):
        styles = (DEMO / "styles.css").read_text(encoding="utf-8")
        app = (DEMO / "app.js").read_text(encoding="utf-8")
        wix = (DEMO / "wix-app" / "site" / "fortune-guide-element.js").read_text(encoding="utf-8")
        self.assertIn(".guide-panel.is-expanded", styles)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto auto", styles)
        self.assertIn(".guide-body", styles)
        self.assertIn("overflow-y: auto", styles)
        self.assertIn('panel.classList.add("is-expanded")', app)
        self.assertIn('panel.classList.remove("is-expanded")', app)
        self.assertIn("options.revealStart", app)
        self.assertIn("articleRect.top - bodyRect.top", app)
        self.assertIn('.panel.expanded', wix)
        self.assertIn("this.revealResult()", wix)

    def test_frontend_styles_preserve_responsive_and_accessibility_states(self):
        styles = (DEMO / "styles.css").read_text(encoding="utf-8")
        wix = (DEMO / "wix-app" / "site" / "fortune-guide-element.js").read_text(encoding="utf-8")
        dashboard = (DEMO / "wix-app" / "dashboard" / "provider-settings.html").read_text(encoding="utf-8")
        for expected in (
            "content-visibility: auto",
            "contain: layout paint",
            "scrollbar-gutter: stable",
            "flex-wrap: nowrap",
            "@media (prefers-reduced-motion: reduce)",
            "@media (forced-colors: active)",
        ):
            self.assertIn(expected, styles)
        self.assertIn("height: calc(100dvh - 16px)", styles)
        self.assertIn("height: calc(100dvh - 16px)", wix)
        self.assertIn(":focus-visible", wix)
        self.assertIn(":focus-visible", dashboard)

    def test_mobile_guide_prioritizes_model_text_over_composer_height(self):
        styles = (DEMO / "styles.css").read_text(encoding="utf-8")
        wix = (DEMO / "wix-app" / "site" / "fortune-guide-element.js").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 520px)", styles)
        self.assertIn("height: min(620px, calc(100dvh - 16px))", styles)
        self.assertIn(".guide-panel.is-expanded", styles)
        self.assertIn(".guide-header, .guide-body, .chat-form, .guide-footer", styles)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 72px", styles)
        self.assertIn(".guide-panel.is-expanded .guide-body", styles)
        self.assertIn(".guide-panel.is-expanded .privacy-copy", styles)
        self.assertNotIn(".chat-input-row { grid-template-columns: 1fr; }", styles)
        self.assertIn(".send { width: 72px;", wix)

    def test_pages_prepare_the_live_backend_connection_before_loading_css(self):
        html = (DEMO / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="preconnect"', html)
        self.assertIn('rel="dns-prefetch"', html)
        self.assertLess(html.index('rel="preconnect"'), html.index('rel="stylesheet"'))
        self.assertNotIn("OLLAMA_API_KEY", html)

    def test_member_access_appears_once_at_the_top_and_supports_profile_state(self):
        html = (DEMO / "index.html").read_text(encoding="utf-8")
        site = (DEMO / "site.js").read_text(encoding="utf-8")
        self.assertEqual(html.count("Create an Account"), 1)
        self.assertEqual(html.count("Sign In"), 1)
        self.assertEqual(html.count(">Profile<"), 1)
        self.assertLess(html.index("Create an Account"), html.index('<header class="site-header">'))
        self.assertIn("fortune:memberstate", site)
        self.assertIn("memberProfile.hidden = !signedIn", site)
        self.assertIn("memberSignedOut.hidden = signedIn", site)

    def test_frontend_renders_clarification_choices_and_related_links(self):
        app = (DEMO / "app.js").read_text(encoding="utf-8")
        self.assertIn("data.choices", app)
        self.assertIn("data.related", app)
        self.assertIn("page_context: pageContext()", app)

    def test_frontend_formats_answers_and_gives_the_prompt_room(self):
        app = (DEMO / "app.js").read_text(encoding="utf-8")
        core = (DEMO / "guide-core.js").read_text(encoding="utf-8")
        styles = (DEMO / "styles.css").read_text(encoding="utf-8")
        self.assertIn("Core.answerPresentation(safeMessage)", app)
        self.assertIn('document.createElement("ul")', app)
        self.assertIn('document.createElement("strong")', app)
        self.assertNotIn("innerHTML", app)
        self.assertIn("DISPLAY_MESSAGE_WORD_LIMIT = 48", core)
        self.assertIn("Who do you need to reach?", core)
        self.assertIn(".answer-list", styles)
        self.assertIn(".answer-note", styles)
        self.assertIn("padding: 10px 12px 14px", styles)

    def test_pages_and_wix_preload_the_model_without_a_provider_key(self):
        app = (DEMO / "app.js").read_text(encoding="utf-8")
        wix = (DEMO / "wix-app" / "site" / "fortune-guide-element.js").read_text(encoding="utf-8")
        self.assertIn('apiUrl("/api/warmup")', app)
        self.assertLess(app.index("warmupPromise = warmModel"), app.index("window.FortuneGuide ="))
        self.assertIn('this.apiUrl("/api/warmup")', wix)
        self.assertNotIn("OLLAMA_API_KEY", app)
        self.assertNotIn("OLLAMA_API_KEY", wix)

    def test_public_view_masks_model_status_and_local_dev_defaults_to_admin(self):
        html = (DEMO / "index.html").read_text(encoding="utf-8")
        app = (DEMO / "app.js").read_text(encoding="utf-8")
        site = (DEMO / "site.js").read_text(encoding="utf-8")
        core = (DEMO / "guide-core.js").read_text(encoding="utf-8")
        self.assertIn('id="viewer-filter"', html)
        self.assertIn('value="public">Public view', html)
        self.assertIn('const viewerMode = Core.viewerMode(window.location.hostname', app)
        self.assertIn('viewerFilter.hidden = !isAdminView', app)
        self.assertIn('if (!isAdminView)', app)
        self.assertIn('modelStatus.textContent = `${activeModelName} · ready`', app)
        self.assertNotIn("Live ${data.model", app)
        self.assertIn('host === "127.0.0.1"', core)
        self.assertIn('return local ? "admin" : "public"', core)
        self.assertIn('url.searchParams.set("view", VIEWER_OVERRIDE)', site)

    def test_static_fallback_is_staged_and_never_ends_without_a_route(self):
        app = (DEMO / "app.js").read_text(encoding="utf-8")
        site = (DEMO / "site.js").read_text(encoding="utf-8")
        fallback = site[site.index("function staticAnswer") : site.index("function selectedUrl")]
        self.assertNotIn("const FAQS", app)
        self.assertLess(fallback.index("ambiguityAnswer"), fallback.index("rankPages"))
        self.assertIn("onCurrentPage", fallback)
        self.assertIn("fallbackDestination", fallback)
        self.assertIn("sources:", fallback)
        self.assertIn("related:", fallback)
        self.assertIn("handoff_url:", fallback)
        self.assertIn("model_called: false", fallback)
        self.assertIn("const BOT_MESSAGE_WORD_LIMIT = 48", site)
        self.assertIn("message: clipWords(message, BOT_MESSAGE_WORD_LIMIT)", fallback)
        self.assertIn("distinctDestination(data)", app)

    def test_page_families_supply_specific_chat_prompts(self):
        core = (DEMO / "guide-core.js").read_text(encoding="utf-8")
        for prompt in (
            "What about this class?",
            "Device or computer help?",
            "What help do you need?",
            "Which class or date?",
            "How can we help you register?",
            "Who do you need to reach?",
            "What about this event?",
            "What current information do you need?",
        ):
            self.assertIn(prompt, core)
        self.assertIn("title.textContent = starter.heading", (DEMO / "app.js").read_text(encoding="utf-8"))
        self.assertIn("questionField.placeholder = starter.placeholder", (DEMO / "app.js").read_text(encoding="utf-8"))

    def test_client_holds_six_digit_ids_before_any_network_request(self):
        app = (DEMO / "app.js").read_text(encoding="utf-8")
        core = (DEMO / "guide-core.js").read_text(encoding="utf-8")
        ask = app[app.index("async function ask") : app.index("async function checkHealth")]
        self.assertLess(ask.index("personalInformationDetected(value)"), ask.index("remoteAnswer(safeQuestion)"))
        self.assertIn("privacyHold();", ask)
        self.assertIn(r"\d{6}", core)
        self.assertIn(r"\d{3}[-. ]\d{3}", core)
        self.assertIn('normalize("NFKC")', core)
        self.assertIn("before it left this browser", app)

    def test_public_deployment_examples_contain_no_api_key_value(self):
        deployment = DEMO / "deployment"
        for path in deployment.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".js", ".mjs", ".html", ".example"}:
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"(?i)ollama_api_key\s*[:=]\s*['\"][A-Za-z0-9_-]{12,}")

    def test_wix_bundle_collects_the_key_only_in_an_admin_surface(self):
        wix = DEMO / "wix-app"
        dashboard = (wix / "dashboard" / "provider-settings.js").read_text(encoding="utf-8")
        dashboard_html = (wix / "dashboard" / "provider-settings.html").read_text(encoding="utf-8")
        site_element = (wix / "site" / "fortune-guide-element.js").read_text(encoding="utf-8")
        backend = (wix / "velo-backend" / "provider-config.web.js").read_text(encoding="utf-8")
        self.assertIn('type="password"', dashboard_html)
        self.assertIn('autocomplete="new-password"', dashboard_html)
        self.assertNotIn("localStorage", dashboard)
        self.assertNotIn("sessionStorage", dashboard)
        self.assertNotIn("providerKey", site_element)
        self.assertIn("Permissions.Admin", backend)
        self.assertNotIn("Permissions.Anyone", backend)
        self.assertNotIn("getSecretValue", dashboard)
        self.assertNotIn("getSecretValue", site_element)

    def test_railway_manifest_has_a_healthcheck_and_no_secret_values(self):
        manifest = json.loads((DEMO / "railway.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["deploy"]["startCommand"], "python3 server.py")
        self.assertEqual(manifest["deploy"]["healthcheckPath"], "/health")
        env_template = (DEMO / ".env.example").read_text(encoding="utf-8")
        self.assertIn("OLLAMA_API_KEY=", env_template)
        self.assertIn("FORTUNE_MODEL_WARMUP_COOLDOWN=900", env_template)
        self.assertIn("FORTUNE_MODEL_KEEP_ALIVE=30m", env_template)
        self.assertNotRegex(env_template, r"OLLAMA_API_KEY=.+")


if __name__ == "__main__":
    unittest.main(verbosity=2)
