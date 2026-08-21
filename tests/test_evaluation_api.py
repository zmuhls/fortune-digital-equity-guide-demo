#!/usr/bin/env python3
"""Network-level evaluator access and static-file isolation tests."""

import http.client
import json
import pathlib
import sys
import threading
import unittest


DEMO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

import server


class _FakeEvaluationStore:
    enabled = True
    absolute_seconds = 28800
    invite_seconds = 86400

    def __init__(self):
        self.claimed = False
        self.invited_email = None
        self.prompt_proposals = {}

    def public_status(self):
        return {
            "enabled": True,
            "ready": True,
            "total_slots": 4,
            "claimed_slots": 0,
            "unassigned_slots": 4,
        }

    def login(self, email, password):
        if email != "editor@example.org" or password != "correct horse battery":
            raise server.AuthenticationFailed("Email or password was not recognized.")
        return {
            "session_token": "session-token",
            "csrf_token": "csrf-token",
            "account": {
                "slot_key": "editor-1",
                "role": "editor",
                "display_name": "Editor 1",
            },
        }

    def authenticate(self, token):
        if token == "admin-session":
            return {
                "slot_key": "admin",
                "role": "admin",
                "display_name": "Administrator",
            }
        if token == "claimed-session" and self.claimed:
            return {
                "slot_key": "editor-1",
                "role": "editor",
                "display_name": "Tester One",
            }
        if token == "editor-2-session":
            return {
                "slot_key": "editor-2",
                "role": "editor",
                "display_name": "Editor 2",
            }
        if token != "session-token":
            return None
        return {
            "slot_key": "editor-1",
            "role": "editor",
            "display_name": "Editor 1",
        }

    def csrf_token(self, token):
        if token == "admin-session":
            return "admin-csrf"
        if token == "claimed-session" and self.claimed:
            return "claimed-csrf"
        if token == "editor-2-session":
            return "editor-2-csrf"
        return "csrf-token" if token == "session-token" else ""

    def csrf_matches(self, token, supplied):
        return supplied == self.csrf_token(token) and bool(supplied)

    def list_accounts(self):
        return [
            {"slot_key": "admin", "role": "admin", "claimed": True, "invitation_active": False, "disabled": False},
            {"slot_key": "editor-1", "role": "editor", "claimed": self.claimed, "invitation_active": bool(self.invited_email) and not self.claimed, "disabled": False},
        ]

    def issue_invitation(self, slot, *, email=None, actor_slot=None, operation_id=None):
        if slot != "editor-1" or actor_slot != "admin" or not operation_id:
            raise AssertionError("Invitation scope was not preserved")
        self.invited_email = email
        return "single-use-token-value-that-is-long-enough"

    def claim_invitation(self, token, email, display_name, password):
        if (
            token != "single-use-token-value-that-is-long-enough"
            or email != self.invited_email
            or display_name != "Tester One"
            or password != "correct horse battery"
            or self.claimed
        ):
            raise server.AuthenticationFailed("This invitation is invalid or expired.")
        self.claimed = True
        return {
            "session_token": "claimed-session",
            "csrf_token": "claimed-csrf",
            "account": {
                "slot_key": "editor-1",
                "role": "editor",
                "display_name": "Tester One",
            },
        }

    def logout(self, _token):
        return None

    def list_buckets(self, _slot):
        return []

    def list_conversations(self, _slot, _limit):
        return []

    def save_note(self, slot, conversation_id, note, expected_version, transcript_version, operation_id):
        return {
            "slot": slot,
            "conversation_id": conversation_id,
            "note": note,
            "version": int(expected_version) + 1,
            "transcript_version": int(transcript_version),
            "operation_id": operation_id,
        }

    def save_annotation(
        self, slot, conversation_id, message_id, category, note,
        expected_version, transcript_version, operation_id,
    ):
        return {
            "slot": slot,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "category": category,
            "note": note,
            "version": int(expected_version) + 1,
            "transcript_version": int(transcript_version),
            "operation_id": operation_id,
        }

    def get_prompt_lab(self, slot, deployed_version, behavior_release):
        return {
            "scope": "shared",
            "shared": True,
            "deployed": {
                "version": deployed_version,
                "behavior_release": behavior_release,
                "editable": False,
            },
            "editable_modules": [
                {"key": "style", "label": "Tone and concision", "current_value": "Current style", "current_variant": "concise_conversational", "maximum_length": 500},
            ],
            "code_controlled": ["Grounding and no-guessing rules"],
            "activation": "code_review_and_deploy_only",
            "can_mark_status": slot == "admin",
            "proposals": list(self.prompt_proposals.values()),
        }

    def create_prompt_proposal(
        self, slot, title, module_values, base_version, proposal_id, operation_id
    ):
        created_at = "2026-08-17T20:00:00Z"
        proposal = {
            "id": proposal_id,
            "title": title,
            "module_values": module_values,
            "base_prompt_version": base_version,
            "status": "draft",
            "version": 1,
            "created_by": slot,
            "updated_by": slot,
            "created_at": created_at,
            "updated_at": created_at,
            "comments": [],
            "revisions": [{
                "proposal_version": 1,
                "base_prompt_version": base_version,
                "title": title,
                "module_values": module_values,
                "status": "draft",
                "actor_slot": slot,
                "action": "proposal.create",
                "recorded_at": created_at,
            }],
        }
        self.prompt_proposals[proposal_id] = proposal
        return proposal

    def update_prompt_proposal(
        self, slot, proposal_id, title, module_values, expected_version, operation_id
    ):
        proposal = self.prompt_proposals[proposal_id]
        proposal.update(
            title=title,
            module_values=module_values,
            version=int(expected_version) + 1,
            updated_by=slot,
        )
        proposal["revisions"].insert(0, {
            "proposal_version": proposal["version"],
            "base_prompt_version": proposal["base_prompt_version"],
            "title": title,
            "module_values": module_values,
            "status": proposal["status"],
            "actor_slot": slot,
            "action": "proposal.update",
            "recorded_at": "2026-08-17T20:03:00Z",
        })
        return proposal

    def add_prompt_proposal_comment(self, slot, proposal_id, comment, operation_id):
        stored = {
            "id": "44444444-4444-4444-8444-444444444444",
            "body": comment,
            "actor_slot": slot,
            "proposal_version": self.prompt_proposals[proposal_id]["version"],
            "created_at": "2026-08-17T20:05:00Z",
        }
        self.prompt_proposals[proposal_id]["comments"].append(stored)
        return stored

    def set_prompt_proposal_status(
        self, slot, proposal_id, status, expected_version, operation_id
    ):
        if slot != "admin":
            raise server.EvaluationForbidden("Only the administrator can change proposal status.")
        proposal = self.prompt_proposals[proposal_id]
        proposal.update(
            status=status,
            version=int(expected_version) + 1,
            updated_by=slot,
        )
        proposal["revisions"].insert(0, {
            "proposal_version": proposal["version"],
            "base_prompt_version": proposal["base_prompt_version"],
            "title": proposal["title"],
            "module_values": proposal["module_values"],
            "status": status,
            "actor_slot": slot,
            "action": f"proposal.{status}",
            "recorded_at": "2026-08-17T20:06:00Z",
        })
        return proposal

    def list_accounts(self):
        raise AssertionError("An editor must not reach the account list")


class EvaluationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_store = server.EVALUATION_STORE
        server.EVALUATION_STORE = _FakeEvaluationStore()
        cls.httpd = server.ThreadingServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        server.EVALUATION_STORE = cls.original_store

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body).encode() if body is not None else None
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers.setdefault("Content-Type", "application/json")
            request_headers.setdefault("Content-Length", str(len(payload)))
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        content = response.read()
        result = response.status, dict(response.getheaders()), content
        connection.close()
        return result

    def same_origin_headers(self):
        return {
            "Host": f"127.0.0.1:{self.port}",
            "Origin": f"http://127.0.0.1:{self.port}",
            "Sec-Fetch-Site": "same-origin",
        }

    def test_evaluation_page_is_public_but_cannot_be_framed(self):
        status, headers, body = self.request("GET", "/evaluation")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn(b"Review conversations", body)

    def test_repository_source_and_private_paths_are_not_static_assets(self):
        for path in (
            "/server.py",
            "/conversation_store.py",
            "/evaluation_store.py",
            "/.env.example",
            "/railway.json",
            "/requirements.txt",
            "/migrations/003_evaluator_identity.sql",
            "/migrations/007_prompt_proposals.sql",
            "/migrations/008_remove_handoff_bucket.sql",
            "/migrations/009_human_review_capture.sql",
            "/scripts/issue_evaluator_invite.py",
            "/tests/test_evaluation_api.py",
            "/replica-snapshots/page-home-e6c04f0f.html.gz",
        ):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path)
                self.assertEqual(status, 404)

    def test_login_requires_a_same_origin_browser_request(self):
        status, _, _ = self.request(
            "POST",
            "/api/evaluation/auth/login",
            {"email": "editor@example.org", "password": "correct horse battery"},
        )
        self.assertEqual(status, 403)

    def test_login_cookie_and_session_endpoint_follow_security_contract(self):
        status, headers, body = self.request(
            "POST",
            "/api/evaluation/auth/login",
            {"email": "editor@example.org", "password": "correct horse battery"},
            self.same_origin_headers(),
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("__Host-fs_eval=session-token", cookie)
        for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        payload = json.loads(body)
        self.assertNotIn("session_token", payload)
        self.assertEqual(payload["csrf_token"], "csrf-token")

        status, _, body = self.request(
            "GET",
            "/api/evaluation/session",
            headers={"Cookie": "__Host-fs_eval=session-token"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["account"]["slot_key"], "editor-1")

    def test_admin_issues_one_click_link_and_claimed_session_survives_reload(self):
        store = server.EVALUATION_STORE
        store.claimed = False
        store.invited_email = None
        headers = {
            **self.same_origin_headers(),
            "Cookie": "__Host-fs_eval=admin-session",
            "X-CSRF-Token": "admin-csrf",
        }
        status, _, body = self.request(
            "POST",
            "/api/evaluation/admin/accounts/editor-1/invitation",
            {
                "email": "tester@example.org",
                "operation_id": "33333333-3333-4333-8333-333333333333",
            },
            headers,
        )
        self.assertEqual(status, 201)
        invitation = json.loads(body)
        self.assertEqual(invitation["expires_in_seconds"], 86400)
        self.assertTrue(invitation["invitation_path"].startswith("/evaluation#invite="))
        token = invitation["invitation_path"].split("#invite=", 1)[1]

        status, claim_headers, body = self.request(
            "POST",
            "/api/evaluation/invitations/claim",
            {
                "token": token,
                "email": "tester@example.org",
                "display_name": "Tester One",
                "password": "correct horse battery",
            },
            self.same_origin_headers(),
        )
        self.assertEqual(status, 200)
        self.assertIn("__Host-fs_eval=claimed-session", claim_headers["Set-Cookie"])
        self.assertEqual(json.loads(body)["account"]["slot_key"], "editor-1")

        status, _, body = self.request(
            "GET",
            "/api/evaluation/session",
            headers={"Cookie": "__Host-fs_eval=claimed-session"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["account"]["display_name"], "Tester One")

        status, _, _ = self.request(
            "POST",
            "/api/evaluation/invitations/claim",
            {
                "token": token,
                "email": "tester@example.org",
                "display_name": "Tester One",
                "password": "correct horse battery",
            },
            self.same_origin_headers(),
        )
        self.assertEqual(status, 401)

    def test_mutation_requires_csrf_and_admin_data_is_role_guarded(self):
        headers = {
            **self.same_origin_headers(),
            "Cookie": "__Host-fs_eval=session-token",
        }
        status, _, _ = self.request(
            "POST",
            "/api/evaluation/auth/logout",
            {},
            headers,
        )
        self.assertEqual(status, 403)

        status, _, _ = self.request(
            "GET",
            "/api/evaluation/admin/accounts",
            headers={"Cookie": "__Host-fs_eval=session-token"},
        )
        self.assertEqual(status, 403)

    def test_notes_and_annotations_require_csrf_and_return_bounded_records(self):
        conversation_id = "11111111-1111-4111-8111-111111111111"
        message_id = "22222222-2222-4222-8222-222222222222"
        operation_id = "33333333-3333-4333-8333-333333333333"
        status, _, _ = self.request(
            "PUT",
            f"/api/evaluation/conversations/{conversation_id}/note",
            {
                "note": "The next step was clear.",
                "expected_version": 2,
                "expected_transcript_version": 9,
                "operation_id": operation_id,
            },
            {
                **self.same_origin_headers(),
                "Cookie": "__Host-fs_eval=session-token",
            },
        )
        self.assertEqual(status, 403)

        headers = {
            **self.same_origin_headers(),
            "Cookie": "__Host-fs_eval=session-token",
            "X-CSRF-Token": "csrf-token",
        }
        status, _, body = self.request(
            "PUT",
            f"/api/evaluation/conversations/{conversation_id}/note",
            {
                "note": "The next step was clear.",
                "expected_version": 2,
                "expected_transcript_version": 9,
                "operation_id": operation_id,
            },
            headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["evaluation"]["version"], 3)

        status, _, body = self.request(
            "PUT",
            f"/api/evaluation/conversations/{conversation_id}/annotations/{message_id}",
            {
                "category": "helpful",
                "note": "Good transition.",
                "expected_version": 0,
                "expected_transcript_version": 9,
                "operation_id": "44444444-4444-4444-8444-444444444444",
            },
            headers,
        )
        self.assertEqual(status, 200)
        annotation = json.loads(body)["annotation"]
        self.assertEqual(annotation["category"], "helpful")
        self.assertEqual(annotation["message_id"], message_id)

    def test_prompt_lab_is_shared_but_status_is_admin_only_and_never_activates(self):
        store = server.EVALUATION_STORE
        store.prompt_proposals = {}
        proposal_id = "11111111-1111-4111-8111-111111111111"
        editor_headers = {
            **self.same_origin_headers(),
            "Cookie": "__Host-fs_eval=session-token",
            "X-CSRF-Token": "csrf-token",
        }
        status, _, body = self.request(
            "POST",
            "/api/evaluation/prompt-proposals",
            {
                "proposal_id": proposal_id,
                "title": "Shorter answers",
                "module_values": {"style": "Use shorter sentences."},
                "operation_id": "22222222-2222-4222-8222-222222222222",
            },
            editor_headers,
        )
        self.assertEqual(status, 201)
        created = json.loads(body)["proposal"]
        self.assertEqual(created["created_by"], "editor-1")
        self.assertEqual(created["status"], "draft")
        self.assertEqual(created["revisions"][0]["proposal_version"], 1)

        status, _, body = self.request(
            "GET",
            "/api/evaluation/prompt-lab",
            headers={"Cookie": "__Host-fs_eval=editor-2-session"},
        )
        self.assertEqual(status, 200)
        shared = json.loads(body)["prompt_lab"]
        self.assertTrue(shared["shared"])
        self.assertEqual(shared["proposals"][0]["id"], proposal_id)
        self.assertEqual(shared["activation"], "code_review_and_deploy_only")

        editor_two_headers = {
            **self.same_origin_headers(),
            "Cookie": "__Host-fs_eval=editor-2-session",
            "X-CSRF-Token": "editor-2-csrf",
        }
        status, _, body = self.request(
            "PUT",
            f"/api/evaluation/prompt-proposals/{proposal_id}",
            {
                "title": "Shorter, clearer answers",
                "module_values": {"style": "Use one or two short sentences."},
                "expected_version": 1,
                "operation_id": "66666666-6666-4666-8666-666666666666",
            },
            editor_two_headers,
        )
        self.assertEqual(status, 200)
        updated = json.loads(body)["proposal"]
        self.assertEqual(updated["updated_by"], "editor-2")
        self.assertEqual(
            [revision["proposal_version"] for revision in updated["revisions"]],
            [2, 1],
        )

        status, _, body = self.request(
            "POST",
            f"/api/evaluation/prompt-proposals/{proposal_id}/comments",
            {
                "comment": "This is easier to scan.",
                "operation_id": "77777777-7777-4777-8777-777777777777",
            },
            editor_two_headers,
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["comment"]["actor_slot"], "editor-2")

        status, _, _ = self.request(
            "PUT",
            f"/api/evaluation/prompt-proposals/{proposal_id}/status",
            {
                "status": "ready",
                "expected_version": 2,
                "operation_id": "33333333-3333-4333-8333-333333333333",
            },
            editor_headers,
        )
        self.assertEqual(status, 403)

        status, _, body = self.request(
            "PUT",
            f"/api/evaluation/prompt-proposals/{proposal_id}/status",
            {
                "status": "ready",
                "expected_version": 2,
                "operation_id": "33333333-3333-4333-8333-333333333333",
            },
            {
                **self.same_origin_headers(),
                "Cookie": "__Host-fs_eval=admin-session",
                "X-CSRF-Token": "admin-csrf",
            },
        )
        self.assertEqual(status, 200)
        ready = json.loads(body)["proposal"]
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(
            [revision["proposal_version"] for revision in ready["revisions"]],
            [3, 2, 1],
        )

        status, _, _ = self.request(
            "PUT",
            f"/api/evaluation/prompt-proposals/{proposal_id}/activate",
            {
                "expected_version": 2,
                "operation_id": "55555555-5555-4555-8555-555555555555",
            },
            {
                **self.same_origin_headers(),
                "Cookie": "__Host-fs_eval=admin-session",
                "X-CSRF-Token": "admin-csrf",
            },
        )
        self.assertEqual(status, 404)

if __name__ == "__main__":
    unittest.main(verbosity=2)
