from __future__ import annotations

import unittest

from filter_rules import MailFilter


class TestMailFilter(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = MailFilter(level="medium")

    def test_list_unsubscribe_is_hard_filtered(self) -> None:
        decision = self.filter.evaluate(
            headers={"List-Unsubscribe": "<mailto:unsubscribe@example.com>"},
            sender="marketing@example.com",
            subject="Weekly newsletter",
            body="Click for deal",
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertFalse(decision.should_reply)
        self.assertEqual(decision.reason, "hard:list-unsubscribe")

    def test_return_path_empty_is_hard_filtered(self) -> None:
        decision = self.filter.evaluate(
            headers={"Return-Path": "<>"},
            sender="system@example.com",
            subject="Account notification",
            body="Your account was updated",
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertFalse(decision.should_reply)
        self.assertEqual(decision.reason, "hard:return-path-empty")

    def test_human_mail_is_allowed(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="friend@example.com",
            subject="你好，想请你帮个忙",
            body="你好，我这边有个需求，方便今天帮我看一下吗？谢谢！",
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.reason, "soft:human-signal")

    def test_allowlist_can_override_low_human_signal(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="friend@example.com",
            subject="ping",
            body="ok",
            allowlist_hit=True,
            frequent_hit=False,
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.reason, "soft:allowlist")

    def test_frequent_sender_can_override_low_human_signal(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="friend@example.com",
            subject="ping",
            body="ok",
            allowlist_hit=False,
            frequent_hit=True,
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.reason, "soft:frequent-sender")

    def test_hard_filter_cannot_be_overridden(self) -> None:
        decision = self.filter.evaluate(
            headers={"Precedence": "bulk", "List-Unsubscribe": "<x>"},
            sender="friend@example.com",
            subject="Hello",
            body="Can we chat?",
            allowlist_hit=True,
            frequent_hit=True,
        )
        self.assertFalse(decision.should_reply)
        self.assertTrue(decision.reason.startswith("hard:"))


if __name__ == "__main__":
    unittest.main()
