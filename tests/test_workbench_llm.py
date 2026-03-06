from __future__ import annotations

import unittest

from workbench.llm_triage import LlmTriageClient
from workbench.sync_service import should_call_llm


class FakeResponse:
    def __init__(self, status_code: int, content: str) -> None:
        self.status_code = status_code
        self._content = content
        self.text = content

    def json(self):  # type: ignore[no-untyped-def]
        return {"choices": [{"message": {"content": self._content}}]}


class TestWorkbenchLLM(unittest.TestCase):
    def test_llm_called_only_for_candidates(self) -> None:
        self.assertFalse(should_call_llm(rule_is_candidate=False, only_candidates=True))
        self.assertTrue(should_call_llm(rule_is_candidate=True, only_candidates=True))
        self.assertTrue(should_call_llm(rule_is_candidate=False, only_candidates=False))

    def test_llm_json_parse_retry_once_then_fallback(self) -> None:
        calls = {"n": 0}

        def fake_request(url, headers, json, timeout):  # type: ignore[no-untyped-def]
            del url, headers, json, timeout
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResponse(200, "not-json")
            return FakeResponse(
                200,
                '{"category":"action","priority":"high","needs_action":true,"suggested_tasks":["do A"],"due_date_guess":null,"evidence":["x"],"confidence":0.9}',
            )

        client = LlmTriageClient(
            token="t",
            api_url="https://models.github.ai/inference/chat/completions",
            model="openai/gpt-4o-mini",
            request_fn=fake_request,
        )

        result = client.triage("a@example.com", "subject", "body")
        self.assertTrue(result.parse_failed)
        self.assertIsNotNone(result.decision)
        self.assertEqual(calls["n"], 2)

    def test_evidence_required_non_empty(self) -> None:
        def fake_request(url, headers, json, timeout):  # type: ignore[no-untyped-def]
            del url, headers, json, timeout
            return FakeResponse(
                200,
                '{"category":"action","priority":"med","needs_action":true,"suggested_tasks":["follow up"],"due_date_guess":null,"evidence":[],"confidence":0.7}',
            )

        client = LlmTriageClient(
            token="t",
            api_url="https://models.github.ai/inference/chat/completions",
            model="openai/gpt-4o-mini",
            request_fn=fake_request,
        )

        result = client.triage("a@example.com", "subject", "body")
        self.assertIsNotNone(result.decision)
        assert result.decision is not None
        self.assertEqual(result.decision.evidence, ["llm_without_evidence"])


if __name__ == "__main__":
    unittest.main()
