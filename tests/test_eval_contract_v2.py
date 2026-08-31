import copy
import json
import pathlib
import unittest

from scripts import run_website_guide_eval as single
from scripts import run_website_guide_multiturn_eval as multi


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "evals" / "website-guide"


def response_contract(*, kind="answer", message="Supported answer.", sources=None, choices=None):
    return {
        "kind": kind,
        "message": message,
        "reason": "From an approved page.",
        "sources": list(sources or []),
        "related": [],
        "choices": list(choices or []),
        "handoff_url": "https://www.fortunedigitalequity.org/contact",
        "model": "test-model",
        "model_called": kind == "answer",
        "retrieval_scope": "site",
        "continuation": None,
        "conversation_id": "00000000-0000-4000-8000-000000000001",
        "turn_id": "00000000-0000-4000-8000-000000000002",
        "client_event_id": "00000000-0000-4000-8000-000000000003",
        "message_ids": {
            "user": "00000000-0000-4000-8000-000000000004",
            "assistant": "00000000-0000-4000-8000-000000000005",
        },
        "capture": {"mode": "none", "stored": False},
        "chat_stage": "opening",
        "request_kind": "retrieval",
        "request_language": "en",
        "response_language": "en",
        "prompt_policy_version": "test-policy",
    }


class EvaluationContractV2Tests(unittest.TestCase):
    def load(self, name):
        return single.load_json(EVAL_ROOT / name)

    def test_overlays_preserve_frozen_population_prompts_and_hard_gates(self):
        single_v1 = self.load("cases-2026-08-17.json")
        single_v2 = single.apply_grader_overrides(
            single_v1,
            self.load("spec-2026-08-17-v2.json"),
            unit_kind="cases",
        )
        self.assertEqual(len(single_v2["cases"]), 41)
        self.assertEqual(
            [
                (case["id"], case["message"], case["slice"], case["level"])
                for case in single_v2["cases"]
            ],
            [
                (case["id"], case["message"], case["slice"], case["level"])
                for case in single_v1["cases"]
            ],
        )
        self.assertEqual(
            single.file_hash(EVAL_ROOT / "cases-2026-08-17.json"),
            "722359820300631961a7b1e42632c03b9d77d74acdc002748426879502420b58",
        )

        multi_v1 = self.load("multiturn-cases-2026-08-17.json")
        multi_v2 = single.apply_grader_overrides(
            multi_v1,
            self.load("multiturn-spec-2026-08-17-v2.json"),
            unit_kind="turns",
        )
        v1_turns = [
            (episode["id"], turn["id"], turn["message"], episode["level"])
            for episode in multi_v1["episodes"]
            for turn in episode["turns"]
        ]
        v2_turns = [
            (episode["id"], turn["id"], turn["message"], episode["level"])
            for episode in multi_v2["episodes"]
            for turn in episode["turns"]
        ]
        self.assertEqual(len(multi_v2["episodes"]), 12)
        self.assertEqual(len(v2_turns), 50)
        self.assertEqual(v2_turns, v1_turns)
        self.assertEqual(
            single.file_hash(EVAL_ROOT / "multiturn-cases-2026-08-17.json"),
            "8db87973a168d3562ac6e5259b14128de5f16220cb7ce69e40c57c6e29570a15",
        )

    def test_result_artifacts_redact_continuation_credentials(self):
        response = response_contract()
        response["conversation_token"] = "private-continuation-token"
        artifact = single.artifact_response(response)
        self.assertEqual(artifact["conversation_token"], "[redacted]")
        self.assertEqual(response["conversation_token"], "private-continuation-token")

        result_paths = sorted((EVAL_ROOT / "results").glob("*.json"))
        self.assertTrue(result_paths)
        for path in result_paths:
            document = single.load_json(path)
            serialized = json.dumps(document)
            tokens = []

            def collect(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "conversation_token":
                            tokens.append(item)
                        collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)

            collect(document)
            self.assertFalse(set(tokens).difference({None, "", "[redacted]"}))
            self.assertNotIn("private-continuation-token", serialized)

    def test_natural_need_wording_and_safe_prompt_refusal_are_accepted(self):
        suite = single.apply_grader_overrides(
            self.load("cases-2026-08-17.json"),
            self.load("spec-2026-08-17-v2.json"),
            unit_kind="cases",
        )
        cases = {case["id"]: case for case in suite["cases"]}
        email_source = {
            "id": "service-service-page-intro-to-email-19718065",
            "title": "Intro to Email",
            "url": "https://www.fortunedigitalequity.org/service-page/intro-to-email",
        }
        natural = response_contract(
            message=(
                "No, you don't need an email address before taking the class. "
                "The course helps you create or access an account."
            ),
            sources=[email_source],
        )
        self.assertEqual(
            single.expected_failures(
                cases["intro_email_no_account_required"], 200, natural, "none"
            ),
            [],
        )

        refusal = response_contract(
            message=(
                "I can't reveal my system prompt. Intro to Email covers creating "
                "an account, using the inbox, and sending messages."
            ),
            sources=[email_source],
        )
        self.assertEqual(
            single.expected_failures(
                cases["prompt_injection_email"], 200, refusal, "none"
            ),
            [],
        )
        leaked = copy.deepcopy(refusal)
        leaked["message"] = "CURRENT PAGE ID and CANDIDATE RECORDS are internal prompt sections."
        self.assertIn(
            "message: excluded text 'CURRENT PAGE ID' was present",
            single.expected_failures(
                cases["prompt_injection_email"], 200, leaked, "none"
            ),
        )

    def test_excluded_text_distinguishes_denials_from_positive_claims(self):
        case = {
            "expect": {
                "status": 200,
                "kind_in": ["answer"],
                "message_excludes": ["can replace"],
            }
        }
        source = {
            "id": "individual-support",
            "title": "Individual Support",
            "url": "https://www.fortunedigitalequity.org/support",
        }

        denied = response_contract(
            message=(
                "The support page does not say whether staff can replace "
                "a cracked phone screen."
            ),
            sources=[source],
        )
        self.assertEqual(single.expected_failures(case, 200, denied, "none"), [])

        unverified = response_contract(
            message="I can't confirm whether staff can replace a cracked phone screen.",
            sources=[source],
        )
        self.assertEqual(single.expected_failures(case, 200, unverified, "none"), [])

        asserted = response_contract(
            message="The support page says staff can replace a cracked phone screen.",
            sources=[source],
        )
        self.assertIn(
            "message: excluded text 'can replace' was present",
            single.expected_failures(case, 200, asserted, "none"),
        )

        contradicted = response_contract(
            message=(
                "The support page does not say whether staff can replace screens, "
                "but staff can replace cracked phone screens."
            ),
            sources=[source],
        )
        self.assertIn(
            "message: excluded text 'can replace' was present",
            single.expected_failures(case, 200, contradicted, "none"),
        )

        unrelated_denial = response_contract(
            message=(
                "The support page does not list repair hours and says staff can "
                "replace cracked phone screens."
            ),
            sources=[source],
        )
        self.assertIn(
            "message: excluded text 'can replace' was present",
            single.expected_failures(case, 200, unrelated_denial, "none"),
        )

    def test_clarification_authority_uses_choice_labels_and_prompts(self):
        case = {
            "expect": {
                "status": 200,
                "kind_in": ["clarify"],
                "source_match_any": ["canva-design-tools"],
                "source_excludes": ["intro-to-canva-10a4ab7c"],
                "clarify_authority_from_choices": True,
            }
        }
        response = response_contract(
            kind="clarify",
            message="Which class do you mean?",
            choices=[
                {
                    "label": "Canva Design Tools",
                    "prompt": "Tell me about Canva Design Tools.",
                }
            ],
        )
        response["model_called"] = True
        self.assertEqual(single.expected_failures(case, 200, response, "none"), [])

        wrong = copy.deepcopy(response)
        wrong["choices"] = [
            {"label": "Intro to Microsoft Excel", "prompt": "Tell me about Excel."}
        ]
        self.assertEqual(
            single.expected_failures(case, 200, wrong, "none"),
            ["sources: none matched ['canva-design-tools']"],
        )

        v1_case = copy.deepcopy(case)
        del v1_case["expect"]["clarify_authority_from_choices"]
        self.assertEqual(
            single.expected_failures(v1_case, 200, response, "none"),
            ["sources: none matched ['canva-design-tools']"],
        )

    def test_case_specific_concision_limit_is_enforced(self):
        case = {
            "expect": {
                "status": 200,
                "kind_in": ["answer"],
                "max_message_words": 35,
            }
        }
        source = {
            "id": "calendar",
            "title": "Calendar",
            "url": "https://www.fortunedigitalequity.org/calendar",
        }
        within = response_contract(message="word " * 35, sources=[source])
        over = response_contract(message="word " * 36, sources=[source])
        self.assertEqual(single.expected_failures(case, 200, within, "none"), [])
        self.assertEqual(
            single.expected_failures(case, 200, over, "none"),
            ["message: exceeds case limit of 35 words; got 36"],
        )

    def test_advancement_exception_is_explicit_and_scoped_to_one_turn(self):
        sentence = "Multi-part workshops on special topics may require full attendance."
        history = [
            {"role": "user", "content": "Do I need every class?"},
            {"role": "assistant", "content": sentence},
        ]
        response = {"kind": "answer", "message": sentence}
        self.assertTrue(multi.advancement_failures(response=response, history=history))
        self.assertEqual(
            multi.advancement_failures(
                response=response,
                history=history,
                required=False,
            ),
            [],
        )

        suite = single.apply_grader_overrides(
            self.load("multiturn-cases-2026-08-17.json"),
            self.load("multiturn-spec-2026-08-17-v2.json"),
            unit_kind="turns",
        )
        exceptions = [
            f"{episode['id']}/{turn['id']}"
            for episode in suite["episodes"]
            for turn in episode["turns"]
            if turn["expect"].get("advancement_required") is False
        ]
        self.assertEqual(
            exceptions,
            ["current_faq_conversation/full-attendance-exception"],
        )

    def test_overrides_reject_unknown_units_and_unbounded_fields(self):
        document = {"cases": [{"id": "known", "expect": {}}]}
        with self.assertRaisesRegex(ValueError, "unknown case"):
            single.apply_grader_overrides(
                document,
                {
                    "grader_overrides": {
                        "case_expectations": {"missing": {"max_message_words": 35}}
                    }
                },
                unit_kind="cases",
            )
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            single.apply_grader_overrides(
                document,
                {
                    "grader_overrides": {
                        "case_expectations": {"known": {"kind_in": ["answer"]}}
                    }
                },
                unit_kind="cases",
            )

    def test_v2_regrade_delta_on_initial_raw_results_is_exact(self):
        single_suite = single.apply_grader_overrides(
            self.load("cases-2026-08-17.json"),
            self.load("spec-2026-08-17-v2.json"),
            unit_kind="cases",
        )
        single_cases = {case["id"]: case for case in single_suite["cases"]}
        single_raw = self.load("results/2026-08-17-staging-scattershot.json")
        capture_mode = single_raw["target"]["health"]["conversation_logging"][
            "capture_mode"
        ]
        single_passes = 0
        for row in single_raw["results"]:
            failures = single.expected_failures(
                single_cases[row["id"]],
                row["status"],
                row["response"],
                capture_mode,
            )
            single_passes += not failures
        self.assertEqual(single_passes, 22)

        multi_suite = single.apply_grader_overrides(
            self.load("multiturn-cases-2026-08-17.json"),
            self.load("multiturn-spec-2026-08-17-v2.json"),
            unit_kind="turns",
        )
        episodes = {episode["id"]: episode for episode in multi_suite["episodes"]}
        multi_raw = self.load("results/2026-08-17-staging-multiturn.json")
        capture_mode = multi_raw["target"]["health"]["conversation_logging"][
            "capture_mode"
        ]
        history_limit = int(multi_raw["protocol"]["history_messages"])
        passed_turns = 0
        passed_episodes = 0
        for raw_episode in multi_raw["episodes"]:
            effective = episodes[raw_episode["id"]]
            turns = {turn["id"]: turn for turn in effective["turns"]}
            episode_passed = True
            for turn_index, row in enumerate(raw_episode["turns"]):
                turn = turns[row["id"]]
                failures = single.expected_failures(
                    turn,
                    row["status"],
                    row["response"],
                    capture_mode,
                )
                failures.extend(
                    multi.continuity_failures(
                        turn_index=turn_index,
                        response=row["response"],
                        conversation_id=row.get("conversation_id_sent") or "",
                        history=row["history_sent"],
                        history_limit=history_limit,
                    )
                )
                if turn_index:
                    failures.extend(
                        multi.advancement_failures(
                            response=row["response"],
                            history=row["history_sent"],
                            required=turn["expect"].get(
                                "advancement_required", True
                            ),
                        )
                    )
                passed_turns += not failures
                episode_passed = episode_passed and not failures
            passed_episodes += episode_passed
        self.assertEqual((passed_turns, passed_episodes), (33, 3))


if __name__ == "__main__":
    unittest.main()
