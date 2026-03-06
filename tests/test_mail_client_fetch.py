from __future__ import annotations

import unittest
from unittest import mock

from mail_client import QQMailClient


class DummyIMAP:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self.search_args: tuple = ()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, *args, **kwargs):
        del args, kwargs

    def select(self, *args, **kwargs):
        del args, kwargs
        return "OK", [b""]

    def search(self, charset, *criteria):
        self.search_args = (charset,) + criteria
        return "OK", [b""]

    def fetch(self, *args, **kwargs):
        del args, kwargs
        return "OK", []


class TestMailClientFetch(unittest.TestCase):
    def test_fetch_uses_unanswered_flag(self) -> None:
        fake_imap = DummyIMAP()
        with mock.patch("mail_client.imaplib.IMAP4_SSL", return_value=fake_imap):
            client = QQMailClient(
                qq_email="bot@example.com",
                qq_auth_code="x",
                imap_host="imap.example.com",
                imap_port=993,
                smtp_host="smtp.example.com",
                smtp_port=465,
            )
            client.fetch_messages_since(fetch_days=3, max_input_chars=1024)

        since_date = client._since_date(3)
        self.assertEqual(fake_imap.search_args, (None, "UNANSWERED", "SINCE", since_date))


if __name__ == "__main__":
    unittest.main()
