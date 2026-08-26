#!/usr/bin/env python3
"""Security and schema contracts for the evaluator foundation."""

import pathlib
import inspect
import sys
import unittest


DEMO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

import evaluation_store
import prompt_policy


class EvaluationSchemaTests(unittest.TestCase):
    def test_identity_migration_seeds_exactly_four_inert_slots(self):
        sql = (DEMO / "migrations" / "003_evaluator_identity.sql").read_text(
            encoding="utf-8"
        )
        seed = sql.split("INSERT INTO evaluator_accounts", 1)[1].split(
            "ON CONFLICT", 1
        )[0]
        expected_rows = {
            "admin": "admin",
            "editor-1": "editor",
            "editor-2": "editor",
            "editor-3": "editor",
        }
        for slot, role in expected_rows.items():
            self.assertEqual(seed.count(f"('{slot}', '{role}')"), 1)
        self.assertNotIn("@", seed)
        self.assertNotIn("token_urlsafe", seed)
        self.assertIn("password_hash IS NULL OR password_hash LIKE '$argon2id$%'", sql)
        self.assertIn("token_hash CHAR(64) NOT NULL UNIQUE", sql)

    def test_taxonomy_is_reviewer_specific_and_audit_is_append_only(self):
        sql = (DEMO / "migrations" / "004_evaluation_taxonomy.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("account_slot TEXT NOT NULL UNIQUE", sql)
        self.assertIn("PRIMARY KEY (bucket_set_id, conversation_id)", sql)
        self.assertIn("UNIQUE (operation_id)", sql.replace("operation_id UUID NOT NULL UNIQUE", "UNIQUE (operation_id)"))
        self.assertIn("evaluation_audit_events_append_only", sql)
        self.assertIn("'Success'", sql)
        self.assertIn("'Needs work'", sql)
        self.assertIn("'Handoff'", sql)
        self.assertNotIn("conversation_messages.content", sql)

    def test_handoff_taxonomy_migration_returns_placements_to_unreviewed(self):
        sql = (DEMO / "migrations" / "008_remove_handoff_bucket.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("SET bucket_id = NULL", sql)
        self.assertIn("version = ce.version + 1", sql)
        self.assertIn("b.standard_key = 'handoff'", sql)
        self.assertIn("SET archived_at = COALESCE(archived_at, NOW())", sql)
        self.assertIn("starter_version = '2026-08-17-v2'", sql)
        self.assertNotIn("DELETE FROM evaluation_buckets", sql)
        self.assertNotIn("standard_key = 'needs-work'", sql)
        list_source = inspect.getsource(evaluation_store.EvaluationStore.list_buckets)
        self.assertIn("b.archived_at IS NULL", list_source)

    def test_evaluation_schema_version_tracks_the_human_review_migration(self):
        self.assertEqual(
            evaluation_store.EVALUATION_SCHEMA_VERSION,
            "009_human_review_capture",
        )
        self.assertEqual(evaluation_store.COOKIE_NAME, "__Host-fs_eval")

    def test_human_review_migration_preserves_transcripts_and_excludes_tests(self):
        sql = (DEMO / "migrations" / "009_human_review_capture.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("c.client_surface IN ('replica', 'wix')", sql)
        self.assertIn("c.capture_mode = 'transcript'", sql)
        self.assertIn("SET review_state = 'pending'", sql)
        self.assertIn("SET review_state = 'ready'", sql)
        self.assertIn("conversation_turns_ready_is_human", sql)
        self.assertNotIn("DELETE FROM conversation", sql)


class EvaluationStoreBoundaryTests(unittest.TestCase):
    def test_reviewer_queue_accepts_only_public_human_conversations(self):
        eligible_source = inspect.getsource(
            evaluation_store.EvaluationStore._eligible_cte
        )
        detail_source = inspect.getsource(
            evaluation_store.EvaluationStore._current_transcript_version
        )
        for source in (eligible_source, detail_source):
            self.assertIn("c.client_surface IN ('replica', 'wix')", source)
            self.assertNotIn("benchmark", source)
            self.assertNotIn("synthetic", source)

    def test_failed_turn_does_not_hide_reviewable_turns_in_the_same_conversation(self):
        predicate = evaluation_store.REVIEWABLE_TURN_PREDICATE
        eligible_source = evaluation_store.EvaluationStore._eligible_cte()
        current_version_source = inspect.getsource(
            evaluation_store.EvaluationStore._current_transcript_version
        )
        detail_source = inspect.getsource(
            evaluation_store.EvaluationStore.get_conversation
        )

        for clause in (
            "t.status = 'complete'",
            "t.privacy_state = 'clear'",
            "t.review_state = 'ready'",
            "conversation_messages",
        ):
            self.assertIn(clause, predicate)
            self.assertIn(clause, eligible_source)
        for source in (current_version_source, detail_source):
            self.assertIn("REVIEWABLE_TURN_PREDICATE", source)
        self.assertNotIn("HAVING BOOL_AND", eligible_source)
        self.assertNotIn("HAVING BOOL_AND", current_version_source)

    def test_all_evaluators_use_one_shared_review_workspace(self):
        self.assertEqual(evaluation_store.SHARED_BUCKET_OWNER, "admin")
        for method_name in ("_bucket_set_id", "list_buckets", "list_conversations", "get_conversation"):
            source = inspect.getsource(getattr(evaluation_store.EvaluationStore, method_name))
            self.assertIn("SHARED_BUCKET_OWNER", source, method_name)

        save_source = inspect.getsource(evaluation_store.EvaluationStore.save_note)
        self.assertIn("actor_slot", save_source)
        self.assertIn("account_slot", save_source)

    def test_first_shared_writes_serialize_before_reading_an_optional_row(self):
        class RecordingCursor:
            def __init__(self):
                self.calls = []

            def execute(self, query, params):
                self.calls.append((" ".join(query.split()), params))

        cursor = RecordingCursor()
        evaluation_store.EvaluationStore._lock_review_record(
            cursor, "set-1", "conversation-1"
        )
        evaluation_store.EvaluationStore._lock_review_record(
            cursor, "set-1", "conversation-1", "message-1"
        )
        self.assertEqual(len(cursor.calls), 2)
        for query, _ in cursor.calls:
            self.assertIn("pg_advisory_xact_lock", query)
            self.assertIn("hashtextextended", query)
        self.assertEqual(cursor.calls[0][1], ("evaluation:set-1:conversation-1:",))
        self.assertEqual(
            cursor.calls[1][1],
            ("annotation:set-1:conversation-1:message-1",),
        )

        for method_name, row_query in (
            ("save_note", "SELECT bucket_id, note, transcript_version, version"),
            ("move_conversation", "SELECT bucket_id, transcript_version, version"),
            (
                "save_annotation",
                "SELECT message_id, category, note, transcript_version, version",
            ),
        ):
            source = inspect.getsource(
                getattr(evaluation_store.EvaluationStore, method_name)
            )
            self.assertLess(
                source.index("_lock_review_record"), source.index(row_query)
            )

    def test_move_uses_the_same_inactive_conversation_gate_as_other_edits(self):
        source = inspect.getsource(
            evaluation_store.EvaluationStore.move_conversation
        )
        self.assertIn("_current_transcript_version", source)
        self.assertNotIn("SELECT MAX(t.sequence)", source)

    def test_disabled_store_needs_no_database_or_auth_secret(self):
        store = evaluation_store.EvaluationStore(
            database_url="", enabled=False, auth_secret=""
        )
        store.open()
        self.assertFalse(store.ready)
        self.assertEqual(
            store.public_status(),
            {
                "enabled": False,
                "ready": False,
                "total_slots": 4,
                "claimed_slots": 0,
                "unassigned_slots": 4,
            },
        )

    def test_email_name_password_and_uuid_inputs_are_bounded(self):
        self.assertEqual(evaluation_store._normalize_email(" A@Example.org "), "a@example.org")
        self.assertEqual(evaluation_store._display_name("  Student   Delegate "), "Student Delegate")
        self.assertEqual(len(evaluation_store._password("correct horse battery")), 21)
        with self.assertRaises(evaluation_store.EvaluationValidation):
            evaluation_store._normalize_email("not-an-email")
        with self.assertRaises(evaluation_store.EvaluationValidation):
            evaluation_store._password("short")
        with self.assertRaises(evaluation_store.EvaluationValidation):
            evaluation_store._uuid("not-a-uuid", "operation_id")

    def test_notes_and_annotation_categories_are_bounded(self):
        self.assertEqual(
            evaluation_store._reviewer_note(
                "  Clear next step.  ", maximum=1000, label="Note"
            ),
            "Clear next step.",
        )
        self.assertEqual(
            evaluation_store._annotation_category("HELPFUL"), "helpful"
        )
        self.assertIsNone(
            evaluation_store._annotation_category("", allow_empty=True)
        )
        with self.assertRaises(evaluation_store.EvaluationValidation):
            evaluation_store._reviewer_note(
                "x" * 501, maximum=500, label="Annotation note"
            )
        with self.assertRaises(evaluation_store.EvaluationValidation):
            evaluation_store._annotation_category("private-data")

    def test_annotation_migration_is_reviewer_specific_and_transcript_free(self):
        sql = (DEMO / "migrations" / "006_transcript_annotations.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "PRIMARY KEY (bucket_set_id, conversation_id, message_id)", sql
        )
        self.assertIn("conversation.annotation", sql)
        self.assertIn("LENGTH(note) <= 500", sql)
        self.assertNotIn("message_content", sql)
        self.assertNotIn("conversation_messages.content", sql)

    def test_prompt_proposal_migration_is_shared_bounded_and_review_only(self):
        sql = (DEMO / "migrations" / "007_prompt_proposals.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("prompt_review_workspaces", sql)
        self.assertIn("VALUES ('shared', '00000000-0000-4000-8000-000000000001')", sql)
        for key in prompt_policy.PROMPT_LAB_TUNABLE_MODULES:
            self.assertIn(f"- '{key}'", sql)
        self.assertIn("status IN ('draft', 'ready', 'archived')", sql)
        self.assertNotIn("'active'", sql)
        self.assertNotIn("'published'", sql)
        self.assertIn("prompt_proposal_comments_append_only", sql)
        self.assertIn("prompt_proposal_events_append_only", sql)
        self.assertIn("CREATE TABLE prompt_proposal_revisions", sql)
        self.assertIn("prompt_proposals_capture_revision", sql)
        self.assertIn("prompt_proposal_revisions_append_only", sql)
        self.assertIn("PRIMARY KEY (proposal_id, proposal_version)", sql)
        self.assertIn("operation_id UUID NOT NULL UNIQUE", sql)
        self.assertNotIn("conversation_messages.content", sql)

    def test_prompt_module_input_is_registry_bounded(self):
        self.assertEqual(
            evaluation_store.PROMPT_EDITABLE_KEYS,
            prompt_policy.PROMPT_LAB_TUNABLE_MODULES,
        )
        self.assertEqual(
            evaluation_store._prompt_module_values({
                "style": "  Make the answer plainer.  ",
            }),
            {"style": "Make the answer plainer."},
        )
        for invalid in (
            {},
            {"grounding": "Relax source checks."},
            {"privacy": "Collect names."},
            {"style": "x" * 501},
        ):
            with self.subTest(invalid=next(iter(invalid), "empty")):
                with self.assertRaises(evaluation_store.EvaluationValidation):
                    evaluation_store._prompt_module_values(invalid)

    def test_prompt_proposal_first_write_uses_an_advisory_lock(self):
        class RecordingCursor:
            def __init__(self):
                self.calls = []

            def execute(self, query, params):
                self.calls.append((" ".join(query.split()), params))

        cursor = RecordingCursor()
        evaluation_store.EvaluationStore._lock_prompt_proposal(
            cursor, "11111111-1111-4111-8111-111111111111"
        )
        self.assertIn("pg_advisory_xact_lock", cursor.calls[0][0])
        self.assertEqual(
            cursor.calls[0][1],
            ("prompt-proposal:shared:11111111-1111-4111-8111-111111111111",),
        )
        create_source = inspect.getsource(
            evaluation_store.EvaluationStore.create_prompt_proposal
        )
        self.assertLess(
            create_source.index("_lock_prompt_proposal"),
            create_source.index("SELECT 1 FROM prompt_proposals"),
        )

    def test_only_admin_can_mark_prompt_proposals_ready_or_archived(self):
        store = evaluation_store.EvaluationStore(
            database_url="postgresql://unused",
            enabled=True,
            auth_secret="test-secret-value-" * 3,
        )
        with self.assertRaises(evaluation_store.EvaluationForbidden):
            store.set_prompt_proposal_status(
                "editor-1",
                "11111111-1111-4111-8111-111111111111",
                "ready",
                1,
                "22222222-2222-4222-8222-222222222222",
            )

    def test_session_and_csrf_digests_are_purpose_separated(self):
        store = evaluation_store.EvaluationStore(
            database_url="postgresql://unused",
            enabled=True,
            auth_secret="test-secret-value-" * 3,
        )
        session_digest = store._digest("session", "same-token")
        csrf_digest = store._digest("csrf", "same-token")
        self.assertEqual(len(session_digest), 64)
        self.assertNotEqual(session_digest, csrf_digest)
        self.assertTrue(store.csrf_matches("same-token", csrf_digest))
        self.assertFalse(store.csrf_matches("same-token", session_digest))

    def test_claimed_account_reset_revokes_sessions_without_deleting_reviewer_data(self):
        source = (DEMO / "evaluation_store.py").read_text(encoding="utf-8")
        script = (DEMO / "scripts" / "reset_evaluator_invite.py").read_text(
            encoding="utf-8"
        )
        reset = source.split("def reset_account_invitation", 1)[1].split(
            "def list_accounts", 1
        )[0]
        self.assertIn("UPDATE evaluator_sessions", reset)
        self.assertIn("revoked_at = NOW()", reset)
        self.assertIn("auth_version = auth_version + 1", reset)
        self.assertIn("password_hash = NULL", reset)
        self.assertIn("claimed_at = NULL", reset)
        self.assertNotIn("DELETE FROM", reset)
        self.assertIn("credential_reset", reset)
        self.assertIn("--confirm-reset", script)


class EvaluationFrontendContractTests(unittest.TestCase):
    def test_review_surface_fits_multiple_buckets_and_stays_concise(self):
        html = (DEMO / "evaluation.html").read_text(encoding="utf-8")
        css = (DEMO / "evaluation.css").read_text(encoding="utf-8")
        javascript = (DEMO / "evaluation.js").read_text(encoding="utf-8")
        self.assertIn("Review conversations", html)
        self.assertNotIn("Conversation queue", html)
        self.assertNotIn("conversation-filter", html)
        self.assertIn(
            "repeat(auto-fit, minmax(min(340px, 100%), 1fr))",
            css,
        )
        self.assertIn(
            "repeat(auto-fit, minmax(min(300px, 100%), 1fr))",
            css,
        )
        self.assertIn("align-items: start", css)
        self.assertIn(".bucket-cards:empty { display: none; }", css)
        self.assertNotIn("min-height: 760px", css)
        self.assertNotIn("min-height: 365px", css)
        self.assertNotIn("min-height: 285px", css)
        self.assertIn('{ id: null, label: "Not yet reviewed"', javascript)
        for label in ("Success", "Needs work"):
            self.assertIn(f'label: "{label}"', javascript)
        self.assertNotIn('label: "Handoff"', javascript)
        self.assertNotIn('"handoff"', javascript)
        self.assertNotIn('label: "Mostly works"', javascript)
        self.assertIn('addEventListener("drop"', javascript)
        self.assertIn("card-move", javascript)
        self.assertIn("conversation.evaluation_version = Number(evaluation.version", javascript)
        self.assertIn('id="bucket-visibility"', html)
        self.assertIn('id="bucket-sort"', html)
        self.assertIn('id="bucket-layout"', html)
        self.assertIn('board[data-layout="compact"]', css)
        self.assertIn('layout: "compact"', javascript)
        self.assertIn('previewKey = "fortune-evaluation-preview-v5"', javascript)
        self.assertIn('viewKeyPrefix = "fortune-evaluation-view-v2"', javascript)
        self.assertIn("const UNREVIEWED_PAGE_SIZE = 8", javascript)
        self.assertIn('api("/api/evaluation/conversations?limit=500")', javascript)
        self.assertIn("items.slice(start, end)", javascript)
        self.assertIn('aria-label="Not yet reviewed pages"', javascript)
        self.assertIn('aria-current="page"', javascript)
        self.assertIn('class="pagination-button pagination-next"', javascript)
        self.assertIn(".bucket-pagination", css)
        self.assertIn("min-height: 44px", css)
        pagination_handler = javascript.split(
            'board.querySelectorAll(".bucket-pagination [data-page]")', 1
        )[1].split("async function moveConversation", 1)[0]
        self.assertNotIn("api(", pagination_handler)
        self.assertNotIn("previewSave", pagination_handler)
        store_source = (DEMO / "evaluation_store.py").read_text(encoding="utf-8")
        self.assertIn("min(int(limit), 500)", store_source)
        self.assertIn('id="review-note"', html)
        self.assertIn('maxlength="1000"', html)
        self.assertIn("annotation-toggle", javascript)
        self.assertIn('maxlength="500"', javascript)
        self.assertIn("localStorage.setItem", javascript)
        self.assertIn('class="invite-form"', javascript)
        self.assertIn('"invitation_path"', (DEMO / "server.py").read_text(encoding="utf-8"))
        self.assertIn("Link ready · single use", javascript)

    def test_shared_queue_and_transcripts_show_stored_time_newest_first(self):
        store_source = (DEMO / "evaluation_store.py").read_text(encoding="utf-8")
        javascript = (DEMO / "evaluation.js").read_text(encoding="utf-8")
        html = (DEMO / "evaluation.html").read_text(encoding="utf-8")

        self.assertIn("ORDER BY e.last_turn_at DESC, e.id", store_source)
        self.assertIn("m.created_at", store_source)
        self.assertIn("t.app_version", store_source)
        self.assertIn("t.prompt_policy_version", store_source)
        self.assertIn("ORDER BY t.sequence, m.ordinal", store_source)
        self.assertIn("function newestFirst(items)", javascript)
        self.assertIn(
            "timestampValue(right.last_turn_at) - timestampValue(left.last_turn_at)",
            javascript,
        )
        self.assertIn("return newestFirst(matches)", javascript)
        self.assertIn(
            'timeHtml(conversation.last_turn_at, "conversation-time")',
            javascript,
        )
        self.assertIn('timeHtml(message.created_at, "message-time")', javascript)
        self.assertIn("readableTimestamp(detail.last_turn_at)", javascript)
        self.assertIn("versionLabel(conversation)", javascript)
        self.assertIn("versionLabel(detail, true)", javascript)
        self.assertIn('class="conversation-version"', javascript)
        self.assertIn('class="message-version"', javascript)
        self.assertIn("20260826-digital-equity-calendar-1", html)

    def test_conversation_queue_refreshes_when_reviewers_return_to_it(self):
        javascript = (DEMO / "evaluation.js").read_text(encoding="utf-8")

        self.assertIn("WORKSPACE_REFRESH_INTERVAL_MS", javascript)
        self.assertIn("async function refreshVisibleWorkspace", javascript)
        self.assertIn('window.addEventListener("focus"', javascript)
        self.assertIn('document.addEventListener("visibilitychange"', javascript)
        self.assertIn("refreshVisibleWorkspace(true)", javascript)
        self.assertNotIn("setInterval(", javascript)

    def test_reviewer_notes_and_annotations_are_reloaded_after_saving(self):
        javascript = (DEMO / "evaluation.js").read_text(encoding="utf-8")
        html = (DEMO / "evaluation.html").read_text(encoding="utf-8")

        self.assertIn("Shared reviewer note", html)
        self.assertIn("async function refreshOpenConversation()", javascript)
        self.assertGreaterEqual(
            javascript.count("await refreshOpenConversation();"),
            2,
        )
        self.assertIn('reviewNoteStatus.textContent = "Unsaved changes"', javascript)
        self.assertIn('reviewNoteStatus.textContent = "Saved to shared review"', javascript)
        self.assertIn('class="save-status annotation-status"', javascript)
        self.assertIn("showAnnotationEditor(", javascript)
        self.assertIn(
            'Object.prototype.hasOwnProperty.call(evaluation, "note")',
            javascript,
        )

    def test_prompt_lab_is_compact_shared_and_has_no_activation_control(self):
        html = (DEMO / "evaluation.html").read_text(encoding="utf-8")
        css = (DEMO / "evaluation.css").read_text(encoding="utf-8")
        javascript = (DEMO / "evaluation.js").read_text(encoding="utf-8")
        server_source = (DEMO / "server.py").read_text(encoding="utf-8")

        self.assertIn('id="prompt-lab-tab"', html)
        self.assertIn('id="prompt-lab-panel"', html)
        self.assertIn('tabindex="-1">Prompts</button>', html)
        self.assertIn("<h2>Prompts</h2>", html)
        self.assertNotIn(">Prompt Lab<", html)
        self.assertIn("Current compiled prompt", html)
        self.assertIn("20260826-prompts-1", html)
        self.assertIn('version: "2026-08-26-v24"', javascript)
        self.assertIn(
            'behavior_release: "digital-equity-current-calendar"', javascript
        )
        self.assertIn(
            'current_variant: "open_conversation_or_blocking_ambiguity"',
            javascript,
        )
        self.assertIn('current_variant: "sitewide_evidence_first"', javascript)
        self.assertIn("Production changes still require code review", html)
        self.assertIn("module-diff-columns", css)
        self.assertIn("Current ·", javascript)
        self.assertIn('class="compiled-prompt-card"', javascript)
        self.assertIn("System prompt · read only", javascript)
        self.assertIn("max-height: 360px", css)
        self.assertIn("Proposed", javascript)
        self.assertIn('api("/api/evaluation/prompt-lab")', javascript)
        self.assertIn('api("/api/evaluation/prompt-proposals"', javascript)
        self.assertIn("Mark ready", javascript)
        self.assertIn("Version history", javascript)
        self.assertIn('event.key === "ArrowLeft"', javascript)
        self.assertIn('event.key === "ArrowRight"', javascript)
        self.assertIn('event.key === "Home"', javascript)
        self.assertIn('event.key === "End"', javascript)
        self.assertIn('id="prompt-lab-tab" type="button" role="tab" aria-selected="false" aria-controls="prompt-lab-panel" tabindex="-1"', html)
        self.assertNotIn("Activate proposal", html + javascript)
        self.assertNotIn("/activate", server_source)
        for forbidden in ("grounding", "privacy", "source_allowlist"):
            self.assertNotIn(f'name="{forbidden}"', html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
