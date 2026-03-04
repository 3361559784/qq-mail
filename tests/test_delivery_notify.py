from __future__ import annotations

import unittest

from mail_client import QQMailClient


class TestDeliveryNotify(unittest.TestCase):
    def setUp(self) -> None:
        self.client = QQMailClient(
            qq_email="bot@qq.com",
            qq_auth_code="x",
            imap_host="imap.qq.com",
            imap_port=993,
            smtp_host="smtp.qq.com",
            smtp_port=465,
        )

    def test_build_delivery_receipt_email_contains_required_fields(self) -> None:
        mail = self.client.build_delivery_receipt_email(
            notify_to="owner@qq.com",
            replied_to="alice@example.com",
            original_subject="测试主题",
            final_body="你好，这是自动回复内容。",
            used_model="openai/gpt-4o",
            dedupe_key="<id-1@example.com>",
            body_chars=1200,
        )

        self.assertEqual(mail["To"], "owner@qq.com")
        self.assertIn("alice@example.com", mail["Subject"])
        text = mail.get_content()
        self.assertIn("收件人: alice@example.com", text)
        self.assertIn("模型: openai/gpt-4o", text)
        self.assertIn("判重键: <id-1@example.com>", text)
        self.assertIn("你好，这是自动回复内容。", text)

    def test_build_delivery_receipt_email_truncates_body_preview(self) -> None:
        mail = self.client.build_delivery_receipt_email(
            notify_to="owner@qq.com",
            replied_to="alice@example.com",
            original_subject="subject",
            final_body="A" * 200,
            used_model="openai/gpt-4o",
            dedupe_key="id-2",
            body_chars=80,
        )

        text = mail.get_content()
        self.assertIn("A" * 80 + "...", text)
        self.assertNotIn("A" * 200, text)


if __name__ == "__main__":
    unittest.main()
