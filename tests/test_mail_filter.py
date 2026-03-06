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
            denylist_hit=False,
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
            denylist_hit=False,
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
            denylist_hit=False,
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.reason, "soft:human-signal")

    def test_allowlist_can_override_low_human_signal(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="friend@example.com",
            subject="status",
            body="id42",
            denylist_hit=False,
            allowlist_hit=True,
            frequent_hit=False,
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.reason, "soft:allowlist")

    def test_frequent_sender_can_override_low_human_signal(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="friend@example.com",
            subject="status",
            body="id42",
            denylist_hit=False,
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
            denylist_hit=False,
            allowlist_hit=True,
            frequent_hit=True,
        )
        self.assertFalse(decision.should_reply)
        self.assertTrue(decision.reason.startswith("hard:"))

    def test_sender_denylist_blocks(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="notifications@github.com",
            subject="reply please",
            body="human-like body",
            denylist_hit=True,
            allowlist_hit=True,
            frequent_hit=True,
        )
        self.assertFalse(decision.should_reply)
        self.assertEqual(decision.reason, "hard:sender-denylist")

    def test_marketing_body_is_hard_filtered(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="promo@example.com",
            subject="hello",
            body="Limited time discount! click here https://a.com?utm_source=x and unsubscribe now.",
            denylist_hit=False,
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertFalse(decision.should_reply)
        self.assertEqual(decision.reason, "hard:marketing-body")

    def test_short_human_message_is_allowed(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="friend@example.com",
            subject="测试",
            body="在吗？",
            denylist_hit=False,
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.reason, "soft:short-human-signal")

    def test_short_human_message_with_iphone_footer_is_allowed(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="friend@example.com",
            subject="测试",
            body="测试azure连通性\n\n发自我的iPhone",
            denylist_hit=False,
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.reason, "soft:short-human-signal")

    def test_marketing_question_is_hard_filtered(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="promo@example.com",
            subject="Can I help you save 50% today?",
            body="Click here now: https://a.com?utm_source=mail",
            denylist_hit=False,
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertFalse(decision.should_reply)
        self.assertEqual(decision.reason, "hard:marketing-body")

    def test_human_notice_subject_is_not_hard_filtered(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="colleague@example.com",
            subject="会议通知：下午三点讨论",
            body="下午三点会议室见，讨论方案细节。",
            denylist_hit=False,
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertTrue(decision.should_reply)
        self.assertNotIn("hard:", decision.reason)

    def test_system_newsletter_subject_needs_list_marker(self) -> None:
        decision = self.filter.evaluate(
            headers={"List-Unsubscribe": "<mailto:unsubscribe@example.com>"},
            sender="updates@example.com",
            subject="Weekly newsletter update",
            body="Here is your weekly digest.",
            denylist_hit=False,
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertFalse(decision.should_reply)
        self.assertEqual(decision.reason, "hard:list-unsubscribe")

    def test_otp_subject_is_blocked(self) -> None:
        decision = self.filter.evaluate(
            headers={},
            sender="security@example.com",
            subject="Verification code: 123456",
            body="Your OTP is 123456, valid for 5 minutes.",
            denylist_hit=False,
            allowlist_hit=False,
            frequent_hit=False,
        )
        self.assertFalse(decision.should_reply)
        self.assertEqual(decision.reason, "hard:otp-subject")


if __name__ == "__main__":
    unittest.main()
