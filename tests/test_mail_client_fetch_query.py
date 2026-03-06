from __future__ import annotations

from unittest.mock import patch
import unittest

from mail_client import QQMailClient


class _FakeIMAP:
    def __init__(self, *_args, **_kwargs) -> None:
        self.search_args = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, *_args, **_kwargs):
        return "OK", [b""]

    def select(self, *_args, **_kwargs):
        return "OK", [b""]

    def search(self, *args):
        self.search_args = args
        return "OK", [b""]


class TestMailClientFetchQuery(unittest.TestCase):
    def test_fetch_query_includes_unanswered(self) -> None:
        fake = _FakeIMAP()
        client = QQMailClient(
            qq_email="bot@qq.com",
            qq_auth_code="x",
            imap_host="imap.qq.com",
            imap_port=993,
            smtp_host="smtp.qq.com",
            smtp_port=465,
        )

        with patch("mail_client.imaplib.IMAP4_SSL", return_value=fake):
            mails = client.fetch_messages_since(fetch_days=1, max_input_chars=2000)

        self.assertEqual(mails, [])
        self.assertIsNotNone(fake.search_args)
        self.assertEqual(fake.search_args[1], "SINCE")
        self.assertEqual(fake.search_args[3], "UNANSWERED")


if __name__ == "__main__":
    unittest.main()
