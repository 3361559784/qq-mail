from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mail_client import IncomingMail
import runner


class _FakeStateStore:
    def __init__(self) -> None:
        self.marked: list[tuple[str, str]] = []
        self.unmarked: list[str] = []
        self._keys: set[str] = set()

    def is_processed(self, dedupe_key: str) -> bool:
        return dedupe_key in self._keys

    def mark_processed(self, dedupe_key: str, sender_email: str) -> bool:
        if dedupe_key in self._keys:
            return False
        self._keys.add(dedupe_key)
        self.marked.append((dedupe_key, sender_email))
        return True

    def unmark_processed(self, dedupe_key: str) -> bool:
        if dedupe_key not in self._keys:
            return False
        self._keys.remove(dedupe_key)
        self.unmarked.append(dedupe_key)
        return True


class _FakeFrequentStore:
    def is_frequent(self, sender_email: str) -> bool:
        return False

    def record(self, sender_email: str) -> None:
        return


class _FakeAllowlist:
    def contains(self, sender_email: str) -> bool:
        return False


class _FakeDenylist:
    def contains(self, sender_email: str) -> bool:
        return False


class _FakeFilter:
    def evaluate(self, **kwargs):
        del kwargs
        return SimpleNamespace(should_reply=True, reason="soft:human-signal", confidence=0.9)


class _FakeModelClient:
    def generate_reply(self, sender: str, subject: str, body: str):
        del sender, subject, body
        return SimpleNamespace(text="ok", used_model="mock-model", attempted_models=["mock-model"])


class _FakeMailClient:
    def __init__(self, should_fail_send: bool) -> None:
        self.should_fail_send = should_fail_send

    def fetch_messages_since(self, fetch_days: int, max_input_chars: int):
        del fetch_days, max_input_chars
        return [
            IncomingMail(
                uid="100",
                dedupe_key="dedupe-1",
                sender_name="Alice",
                sender_email="alice@example.com",
                sender_display="Alice",
                subject="hello",
                body="Need your help",
                headers={},
                original=SimpleNamespace(get=lambda *_args, **_kwargs: ""),
            )
        ]

    def build_reply_email(self, original, to_addr: str, final_body: str):
        del original, to_addr, final_body
        return object()

    def send_email(self, mail) -> None:
        del mail
        if self.should_fail_send:
            raise RuntimeError("smtp down")

    def mark_answered(self, uid: str) -> None:
        del uid


class TestRunnerStateSemantics(unittest.TestCase):
    def _settings(self):
        return SimpleNamespace(
            github_token="x",
            github_api_url="https://example.com",
            github_model_primary="mock",
            github_model_fallbacks=[],
            model_request_timeout_seconds=5,
            personalization_dir="personalization",
            qq_email="bot@qq.com",
            qq_auth_code="x",
            imap_host="imap.qq.com",
            imap_port=993,
            smtp_host="smtp.qq.com",
            smtp_port=465,
            storage_backend="file",
            table_connection_string="",
            processed_state_file="data/processed.json",
            processed_table_name="processed",
            allow_senders_file="data/allow.txt",
            deny_senders_file="data/deny.txt",
            frequent_sender_file="data/frequent.json",
            frequent_window_days=30,
            frequent_min_count=3,
            frequent_max_events=20,
            frequent_table_name="frequent",
            filter_level="medium",
            imap_fetch_days=1,
            max_input_chars=2000,
            reply_signature="-- sig",
            model_signature_template="{model}",
            enable_reply_postprocess=False,
            reply_max_questions=1,
            self_notify_on_reply=False,
            self_notify_email="",
            self_notify_body_chars=1200,
        )

    def test_send_failure_does_not_mark_processed(self) -> None:
        state_store = _FakeStateStore()
        with (
            patch.object(runner, "ModelChainClient", return_value=_FakeModelClient()),
            patch.object(runner, "QQMailClient", return_value=_FakeMailClient(should_fail_send=True)),
            patch.object(runner, "_build_processed_store", return_value=state_store),
            patch.object(runner, "AllowlistStore", return_value=_FakeAllowlist()),
            patch.object(runner, "DenylistStore", return_value=_FakeDenylist()),
            patch.object(runner, "_build_frequent_store", return_value=_FakeFrequentStore()),
            patch.object(runner, "MailFilter", return_value=_FakeFilter()),
        ):
            stats = runner.run_once(self._settings())

        self.assertEqual(stats.errors, 1)
        self.assertEqual(state_store.marked, [("dedupe-1", "alice@example.com")])
        self.assertEqual(state_store.unmarked, ["dedupe-1"])
        self.assertFalse(state_store.is_processed("dedupe-1"))

    def test_send_success_marks_processed(self) -> None:
        state_store = _FakeStateStore()
        with (
            patch.object(runner, "ModelChainClient", return_value=_FakeModelClient()),
            patch.object(runner, "QQMailClient", return_value=_FakeMailClient(should_fail_send=False)),
            patch.object(runner, "_build_processed_store", return_value=state_store),
            patch.object(runner, "AllowlistStore", return_value=_FakeAllowlist()),
            patch.object(runner, "DenylistStore", return_value=_FakeDenylist()),
            patch.object(runner, "_build_frequent_store", return_value=_FakeFrequentStore()),
            patch.object(runner, "MailFilter", return_value=_FakeFilter()),
        ):
            stats = runner.run_once(self._settings())

        self.assertEqual(stats.replied, 1)
        self.assertEqual(state_store.marked, [("dedupe-1", "alice@example.com")])
        self.assertEqual(state_store.unmarked, [])
        self.assertTrue(state_store.is_processed("dedupe-1"))


if __name__ == "__main__":
    unittest.main()
