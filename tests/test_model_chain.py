from __future__ import annotations

import unittest

from model_chain import ModelChainClient


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


class TestModelChain(unittest.TestCase):
    def test_primary_success_without_fallback(self) -> None:
        calls: list[str] = []

        def fake_request(url, headers, json, timeout):  # type: ignore[no-untyped-def]
            del url, headers, timeout
            calls.append(json["model"])
            return FakeResponse(
                200,
                payload={"choices": [{"message": {"content": "primary reply"}}]},
            )

        client = ModelChainClient(
            token="t",
            api_url="https://models.github.ai/inference/chat/completions",
            primary="model-primary",
            fallbacks=["f1", "f2", "f3"],
            request_fn=fake_request,
        )
        result = client.generate_reply("Alice", "Hello", "Need your help")

        self.assertEqual(result.text, "primary reply")
        self.assertEqual(result.used_model, "model-primary")
        self.assertEqual(result.attempted_models, ["model-primary"])
        self.assertEqual(calls, ["model-primary"])

    def test_fallback_after_primary_failure(self) -> None:
        calls: list[str] = []

        def fake_request(url, headers, json, timeout):  # type: ignore[no-untyped-def]
            del url, headers, timeout
            model = json["model"]
            calls.append(model)
            if model == "model-primary":
                return FakeResponse(500, text="upstream error")
            return FakeResponse(
                200,
                payload={"choices": [{"message": {"content": f"reply by {model}"}}]},
            )

        client = ModelChainClient(
            token="t",
            api_url="https://models.github.ai/inference/chat/completions",
            primary="model-primary",
            fallbacks=["model-f1", "model-f2", "model-f3"],
            request_fn=fake_request,
        )
        result = client.generate_reply("Alice", "Hello", "Need your help")

        self.assertEqual(result.used_model, "model-f1")
        self.assertEqual(result.text, "reply by model-f1")
        self.assertEqual(result.attempted_models, ["model-primary", "model-f1"])
        self.assertEqual(calls, ["model-primary", "model-f1"])

    def test_all_models_failed(self) -> None:
        calls: list[str] = []

        def fake_request(url, headers, json, timeout):  # type: ignore[no-untyped-def]
            del url, headers, timeout
            calls.append(json["model"])
            return FakeResponse(503, text="temporary unavailable")

        client = ModelChainClient(
            token="t",
            api_url="https://models.github.ai/inference/chat/completions",
            primary="model-primary",
            fallbacks=["model-f1", "model-f2", "model-f3"],
            request_fn=fake_request,
        )

        with self.assertRaises(RuntimeError) as ctx:
            client.generate_reply("Alice", "Hello", "Need your help")
        self.assertIn("All models failed", str(ctx.exception))
        self.assertEqual(calls, ["model-primary", "model-f1", "model-f2", "model-f3"])


if __name__ == "__main__":
    unittest.main()
