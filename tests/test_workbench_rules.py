from __future__ import annotations

import unittest

from workbench.rules import triage_by_rules


class TestWorkbenchRules(unittest.TestCase):
    def test_rule_triage_action_waiting_fyi_spamish(self) -> None:
        action = triage_by_rules(
            sender_email="boss@example.com",
            subject="请确认截止时间",
            body_text="麻烦今天回复",
            headers_json="{}",
            flags_json="[]",
        )
        self.assertEqual(action.category, "action")

        waiting = triage_by_rules(
            sender_email="peer@example.com",
            subject="Re: status",
            body_text="please confirm this",
            headers_json="{}",
            flags_json='["\\Seen"]',
        )
        self.assertEqual(waiting.category, "waiting")

        fyi = triage_by_rules(
            sender_email="updates@github.com",
            subject="GitHub notification",
            body_text="some update",
            headers_json="{}",
            flags_json="[]",
        )
        self.assertEqual(fyi.category, "fyi")

        spam = triage_by_rules(
            sender_email="promo@example.com",
            subject="limited discount",
            body_text="unsubscribe now",
            headers_json='{"List-Unsubscribe":"<x>"}',
            flags_json="[]",
        )
        self.assertEqual(spam.category, "spamish")


if __name__ == "__main__":
    unittest.main()
