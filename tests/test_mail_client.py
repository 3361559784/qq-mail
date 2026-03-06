from __future__ import annotations

import unittest
from email.message import EmailMessage
from unittest import mock

from mail_client import QQMailClient


class FakeImap:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str | None, list[str]]] = []
        self.fetch_calls: list[tuple[bytes, str]] = []

    def login(self, email, auth_code):  # type: ignore[no-untyped-def]
        del email, auth_code
        return "OK", []

    def select(self, mailbox):  # type: ignore[no-untyped-def]
        del mailbox
        return "OK", []

    def search(self, charset, *criteria):  # type: ignore[no-untyped-def]
        self.search_calls.append((charset, list(criteria)))
        return "OK", [b"1"]

    def fetch(self, msg_num, query):  # type: ignore[no-untyped-def]
        self.fetch_calls.append((msg_num, query))
        msg = EmailMessage()
        msg["From"] = "alice@example.com"
        msg["Subject"] = "Hello"
        msg["Message-ID"] = "<m1@example.com>"
        msg.set_content("body")
        meta = f"{msg_num.decode()} UID 123".encode()
        return "OK", [(meta, msg.as_bytes())]

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        del exc_type, exc, tb
        return False


class TestMailClient(unittest.TestCase):
    @mock.patch("mail_client.imaplib.IMAP4_SSL")
    def test_fetch_messages_since_uses_unanswered_filter(self, mock_imap_ctor) -> None:
        fake_imap = FakeImap()
        mock_imap_ctor.return_value = fake_imap

        client = QQMailClient(
            qq_email="me@example.com",
            qq_auth_code="token",
            imap_host="imap.example.com",
            imap_port=993,
            smtp_host="smtp.example.com",
            smtp_port=465,
        )

        mails = client.fetch_messages_since(fetch_days=1, max_input_chars=200)

        self.assertTrue(fake_imap.search_calls)
        charset, criteria = fake_imap.search_calls[0]
        self.assertIsNone(charset)
        self.assertIn("UNANSWERED", criteria)
        self.assertIn("SINCE", criteria)
        self.assertEqual(len(mails), 1)
        self.assertEqual(mails[0].uid, "123")


if __name__ == "__main__":
    unittest.main()
