from __future__ import annotations

import unittest

from workbench.normalize import build_thread_key, normalize_body_text, stable_content_hash


class TestWorkbenchNormalize(unittest.TestCase):
    def test_thread_key_deterministic(self) -> None:
        k1 = build_thread_key(
            subject="Re: Test Subject",
            sender_email="A@example.com",
            to_emails=["b@example.com"],
            cc_emails=["c@example.com"],
            received_at_utc="2026-03-05T01:00:00Z",
        )
        k2 = build_thread_key(
            subject="test subject",
            sender_email="a@example.com",
            to_emails=["b@example.com"],
            cc_emails=["c@example.com"],
            received_at_utc="2026-03-05T23:00:00Z",
        )
        self.assertEqual(k1, k2)

    def test_body_text_normalization_stable_hash(self) -> None:
        t1 = "你好\n\n\n请确认\n\n发件人: x"
        t2 = "你好\r\n\r\n请确认"
        h1 = stable_content_hash("A", "x@example.com", normalize_body_text(t1))
        h2 = stable_content_hash("A", "x@example.com", normalize_body_text(t2))
        self.assertEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
