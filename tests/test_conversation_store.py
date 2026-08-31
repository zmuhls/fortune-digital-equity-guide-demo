#!/usr/bin/env python3
"""Key-free tests for privacy-bounded conversation persistence."""

import pathlib
import sys
import unittest
import uuid
from unittest import mock


DEMO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

import conversation_store
import server


class _Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


class _RecordingCursor:
    def __init__(self):
        self.calls = []
        self.many = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def executemany(self, query, params):
        self.many.append((query, list(params)))


class _RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return _Context(self)

    def cursor(self, **_kwargs):
        return _Context(self._cursor)


class _RecordingPool:
    def __init__(self):
        self.cursor = _RecordingCursor()
        self.connection_value = _RecordingConnection(self.cursor)

    def connection(self):
        return _Context(self.connection_value)


def persisted_reservation(mode="none", surface="unknown"):
    value = conversation_store.new_reservation(mode=mode, client_surface=surface)
    return conversation_store.TurnReservation(**{
        **value.__dict__,
        "persisted": True,
    })


def recording_recorder(mode):
    recorder = conversation_store.ConversationRecorder(
        database_url="postgresql://not-used",
        mode=mode,
        token_secret="test-secret-" * 4,
    )
    recorder._pool = _RecordingPool()
    recorder._jsonb = lambda value: value
    return recorder


class ConversationStoreTests(unittest.TestCase):
    def test_human_and_automated_surfaces_are_distinct(self):
        self.assertEqual(conversation_store.sanitized_surface("benchmark"), "benchmark")
        self.assertEqual(conversation_store.sanitized_surface("synthetic"), "synthetic")
        self.assertEqual(conversation_store.sanitized_surface("replica"), "replica")
        self.assertEqual(conversation_store.sanitized_surface("wix"), "wix")
        self.assertEqual(
            conversation_store.HUMAN_REVIEW_SURFACES,
            frozenset({"replica", "wix"}),
        )
        self.assertEqual(conversation_store.sanitized_surface("not-allowed"), "unknown")
        self.assertEqual(
            conversation_store.automation_provenance("benchmark"),
            (True, "benchmark"),
        )
        self.assertEqual(
            conversation_store.automation_provenance(
                "replica", "browser webdriver"
            ),
            (True, "browser-webdriver"),
        )
        self.assertEqual(
            conversation_store.automation_provenance("wix"),
            (False, ""),
        )

    def test_capture_is_disabled_by_default_and_needs_no_database(self):
        recorder = conversation_store.ConversationRecorder(
            database_url="",
            mode="none",
        )
        recorder.open()
        turn = recorder.begin_turn(question="A synthetic question")
        self.assertFalse(turn.persisted)
        self.assertFalse(recorder.complete_turn(
            turn,
            question="A synthetic question",
            response={"message": "A synthetic answer"},
            privacy_state="clear",
            latency_ms=1,
        ))

    def test_enabled_capture_requires_database_and_a_long_token_secret(self):
        with self.assertRaises(conversation_store.CaptureUnavailable):
            conversation_store.ConversationRecorder(
                database_url="",
                mode="metadata",
                token_secret="x" * 32,
            ).open()
        with self.assertRaises(conversation_store.CaptureUnavailable):
            conversation_store.ConversationRecorder(
                database_url="postgresql://not-used",
                mode="transcript",
                token_secret="short",
            ).open()

    def test_app_version_uses_a_deployment_identifier_when_git_metadata_is_absent(self):
        with mock.patch.dict(
            "os.environ",
            {
                "RAILWAY_GIT_COMMIT_SHA": "",
                "FORTUNE_APP_VERSION": "",
                "RAILWAY_DEPLOYMENT_ID": "deployment-2026-08-21",
            },
        ):
            recorder = conversation_store.ConversationRecorder(mode="none")
        self.assertEqual(recorder.app_version, "deployment-2026-08-21")

    def test_explicit_release_version_overrides_stale_railway_git_metadata(self):
        with mock.patch.dict(
            "os.environ",
            {
                "FORTUNE_APP_VERSION": "current-release",
                "RAILWAY_GIT_COMMIT_SHA": "stale-connected-repository-sha",
                "RAILWAY_DEPLOYMENT_ID": "deployment-fallback",
            },
        ):
            recorder = conversation_store.ConversationRecorder(mode="none")
        self.assertEqual(recorder.app_version, "current-release")

    def test_conversation_continuation_requires_the_server_token(self):
        recorder = conversation_store.ConversationRecorder(
            database_url="postgresql://not-used",
            mode="metadata",
            token_secret="continuation-secret-" * 2,
        )
        conversation_id = str(uuid.uuid4())
        token = recorder.conversation_token(conversation_id)
        self.assertEqual(
            recorder.accepted_conversation_id(conversation_id, token),
            conversation_id,
        )
        self.assertIsNone(
            recorder.accepted_conversation_id(conversation_id, "wrong-token")
        )

    def test_idempotency_fingerprint_includes_safe_history_context(self):
        base = dict(
            secret="test-secret-" * 4,
            question="Where is the class?",
            page_context={"source_id": "calendar"},
            client_surface="synthetic",
        )
        first = conversation_store.fingerprint_request(
            **base,
            history_context=[{"role": "user", "content": "I need a class."}],
        )
        changed = conversation_store.fingerprint_request(
            **base,
            history_context=[{"role": "user", "content": "I need a device."}],
        )
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, changed)
        automated = conversation_store.fingerprint_request(
            **base,
            history_context=[{"role": "user", "content": "I need a class."}],
            automation_source="fixed-suite",
        )
        self.assertNotEqual(first, automated)

    def test_duplicate_turn_query_qualifies_columns_shared_with_conversations(self):
        source = (DEMO / "conversation_store.py").read_text(encoding="utf-8")
        self.assertIn("t.capture_mode, t.status, t.response_json", source)

    def test_metadata_capture_never_writes_message_content(self):
        recorder = recording_recorder("metadata")
        recorder.complete_turn(
            persisted_reservation("metadata"),
            question="SYNTHETIC QUESTION",
            response={"kind": "answer", "message": "SYNTHETIC ANSWER"},
            privacy_state="clear",
            latency_ms=4,
        )
        self.assertEqual(recorder._pool.cursor.many, [])
        recorded = repr(recorder._pool.cursor.calls)
        self.assertNotIn("SYNTHETIC QUESTION", recorded)
        self.assertNotIn("SYNTHETIC ANSWER", recorded)

    def test_privacy_held_turn_never_writes_message_content_or_token(self):
        recorder = recording_recorder("transcript")
        recorder.complete_turn(
            persisted_reservation("transcript"),
            question="SENSITIVE-SENTINEL",
            response={
                "kind": "privacy",
                "message": "Remove personal information.",
                "conversation_token": "CAPABILITY-SENTINEL",
            },
            privacy_state="blocked",
            latency_ms=5,
        )
        cursor = recorder._pool.cursor
        self.assertEqual(cursor.many, [])
        recorded = repr(cursor.calls)
        self.assertNotIn("SENSITIVE-SENTINEL", recorded)
        self.assertNotIn("CAPABILITY-SENTINEL", recorded)

    def test_transcript_mode_writes_only_a_clear_turns_two_messages(self):
        recorder = recording_recorder("transcript")
        recorder.complete_turn(
            persisted_reservation("transcript"),
            question="SYNTHETIC QUESTION",
            response={"kind": "answer", "message": "SYNTHETIC ANSWER"},
            privacy_state="clear",
            latency_ms=6,
        )
        message_rows = recorder._pool.cursor.many[0][1]
        self.assertEqual([row[4] for row in message_rows], ["user", "assistant"])
        self.assertEqual([row[5] for row in message_rows], [
            "SYNTHETIC QUESTION",
            "SYNTHETIC ANSWER",
        ])

    def test_failed_sensitive_turn_closes_without_storing_messages(self):
        recorder = recording_recorder("transcript")
        recorder.fail_turn(
            persisted_reservation("transcript", "replica"),
            latency_ms=19,
            error_code="model_response_rejected",
            model="test-model",
            model_called=True,
            retrieval_scope="staff",
            privacy_state="sensitive_handoff",
            interaction_context={
                "chat_stage": "opening",
                "request_kind": "sensitive",
                "request_language": "en",
                "prompt_policy_version": "test-policy",
            },
        )
        cursor = recorder._pool.cursor
        self.assertEqual(cursor.many, [])
        query, params = cursor.calls[0]
        self.assertIn("status = 'failed'", query)
        self.assertIn("review_state = 'excluded'", query)
        self.assertEqual(params[0], "sensitive_handoff")
        self.assertEqual(params[1], "staff")
        self.assertTrue(params[3])
        self.assertEqual(params[5], "model_response_rejected")

    def test_failed_clear_human_turn_retains_only_the_visitor_question(self):
        recorder = recording_recorder("transcript")
        recorder.fail_turn(
            persisted_reservation("transcript", "replica"),
            question="CLEAR HUMAN QUESTION",
            latency_ms=21,
            error_code="model_unavailable",
            model="test-model",
            model_called=True,
            retrieval_scope="site",
            privacy_state="clear",
        )
        cursor = recorder._pool.cursor
        insert_query, insert_params = cursor.calls[0]
        self.assertIn("INSERT INTO conversation_messages", insert_query)
        self.assertIn("'user'", insert_query)
        self.assertEqual(insert_params[-1], "CLEAR HUMAN QUESTION")
        self.assertNotIn("assistant", repr(insert_params))
        self.assertIn("status = 'failed'", cursor.calls[1][0])

    def test_failed_automated_turn_does_not_retain_question_text(self):
        recorder = recording_recorder("transcript")
        recorder.fail_turn(
            persisted_reservation("transcript", "benchmark"),
            question="BENCHMARK QUESTION",
            latency_ms=22,
            error_code="model_unavailable",
            model="test-model",
            model_called=True,
            retrieval_scope="site",
            privacy_state="clear",
        )
        self.assertNotIn("BENCHMARK QUESTION", repr(recorder._pool.cursor.calls))

    def test_failed_idempotent_turn_is_terminal_not_in_progress(self):
        source = (DEMO / "conversation_store.py").read_text(encoding="utf-8")
        self.assertIn('if existing["status"] == "failed":', source)
        self.assertIn('"This turn already failed. Send it again as a new turn."', source)

    def test_only_clear_human_transcript_turns_are_review_ready(self):
        cases = (
            ("transcript", "replica", "clear", "ready"),
            ("transcript", "wix", "clear", "ready"),
            ("transcript", "benchmark", "clear", "pending"),
            ("transcript", "synthetic", "clear", "pending"),
            ("metadata", "replica", "clear", "pending"),
            ("transcript", "replica", "blocked", "excluded"),
        )
        for mode, surface, privacy_state, expected_review_state in cases:
            with self.subTest(mode=mode, surface=surface, privacy_state=privacy_state):
                recorder = recording_recorder(mode)
                recorder.complete_turn(
                    persisted_reservation(mode, surface),
                    question="Synthetic question",
                    response={
                        "kind": "answer",
                        "message": "Synthetic answer",
                        "chat_stage": "opening",
                        "request_kind": "retrieval",
                        "request_language": "en",
                        "response_language": "en",
                        "prompt_policy_version": "2026-08-08-v2",
                    },
                    privacy_state=privacy_state,
                    latency_ms=6,
                )
                update_params = recorder._pool.cursor.calls[0][1]
                self.assertEqual(update_params[1], expected_review_state)

    def test_capture_page_context_uses_only_server_index_values(self):
        captured = server.capture_page_context({
            "url": "https://www.fortunedigitalequity.org/devices",
            "path": "/private-value",
            "title": "SENSITIVE-SENTINEL",
        })
        self.assertEqual(captured["source_id"], "devices")
        self.assertEqual(captured["path"], "/devices")
        self.assertNotEqual(captured["title"], "SENSITIVE-SENTINEL")
        self.assertEqual(
            server.capture_page_context({"url": "https://example.com/private"}),
            {"source_id": "", "url": "", "path": "", "title": "", "authority": ""},
        )

    def test_migration_makes_client_event_ids_globally_unique(self):
        migration = (DEMO / "migrations" / "001_conversation_capture.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("client_event_id UUID NOT NULL UNIQUE", migration)
        self.assertIn("request_fingerprint", migration)
        turn_context = (DEMO / "migrations" / "002_turn_page_context.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("ADD COLUMN page_context JSONB", turn_context)
        interaction_context = (
            DEMO / "migrations" / "005_interaction_context.sql"
        ).read_text(encoding="utf-8")
        for column in (
            "chat_stage",
            "request_kind",
            "request_language",
            "response_language",
            "prompt_policy_version",
        ):
            self.assertIn(f"ADD COLUMN {column}", interaction_context)
        self.assertIn("conversation_turns_ready_is_safe", interaction_context)
        self.assertIn("conversation_turns_ready_is_synthetic", interaction_context)
        human_review = (
            DEMO / "migrations" / "009_human_review_capture.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("DROP TRIGGER IF EXISTS conversation_turns_ready_is_synthetic", human_review)
        self.assertIn("conversation_turns_ready_is_human", human_review)
        self.assertIn("c.client_surface IN ('replica', 'wix')", human_review)
        self.assertIn("SET review_state = 'pending'", human_review)
        self.assertIn("SET review_state = 'ready'", human_review)
        self.assertNotIn("DELETE FROM conversation", human_review)
        automation = (
            DEMO / "migrations" / "010_automation_provenance.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN is_automated BOOLEAN", automation)
        self.assertIn("automation_source", automation)
        self.assertIn("client_surface IN ('benchmark', 'synthetic')", automation)
        self.assertNotIn("conversation_messages", automation)
        self.assertEqual(conversation_store.SCHEMA_VERSION, "010_automation_provenance")


if __name__ == "__main__":
    unittest.main(verbosity=2)
