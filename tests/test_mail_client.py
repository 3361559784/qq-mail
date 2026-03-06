from __future__ import annotations

import mail_client
from mail_client import QQMailClient


def test_fetch_messages_uses_unanswered_search(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeIMAP:
        def __init__(self) -> None:
            self.search_args: tuple[str, ...] = ()

        def login(self, *args, **kwargs) -> None:
            del args, kwargs

        def select(self, *args, **kwargs) -> None:
            del args, kwargs

        def search(self, charset, *criteria):  # type: ignore[no-untyped-def]
            del charset
            self.search_args = tuple(criteria)
            return "OK", [b""]

        def fetch(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            return "OK", []

        def __enter__(self) -> "FakeIMAP":
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            del exc_type, exc_value, traceback

    def fake_imap4_ssl(host, port):  # type: ignore[no-untyped-def]
        del host, port
        imap = FakeIMAP()
        calls["imap"] = imap
        return imap

    monkeypatch.setattr(mail_client.imaplib, "IMAP4_SSL", fake_imap4_ssl)

    client = QQMailClient(
        qq_email="user@example.com",
        qq_auth_code="auth",
        imap_host="imap.qq.com",
        imap_port=993,
        smtp_host="smtp.qq.com",
        smtp_port=465,
    )
    messages = client.fetch_messages_since(fetch_days=3, max_input_chars=2000)

    assert messages == []
    fake_imap = calls["imap"]
    assert isinstance(fake_imap, FakeIMAP)
    assert fake_imap.search_args[:2] == ("UNANSWERED", "SINCE")
