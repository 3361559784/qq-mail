from __future__ import annotations

import unittest

from mail_client import sanitize_reply_text


class TestReplyPostprocess(unittest.TestCase):
    def test_keep_single_short_closing_under_medium_clean(self) -> None:
        raw = (
            "您好，\n"
            "链路已收到，我会继续跟进。\n"
            "祝好！\n"
            "祝好，\n"
            "此致\n"
            "敬礼"
        )
        cleaned = sanitize_reply_text(raw)
        self.assertIn("链路已收到，我会继续跟进。", cleaned)
        closing_count = cleaned.count("祝好") + cleaned.count("此致") + cleaned.count("敬礼")
        self.assertEqual(closing_count, 1)

    def test_question_limit_still_enforced(self) -> None:
        raw = "你好，请问你的目标是什么？你希望我先检查模型还是邮件投递？还需要我给你测试清单吗？"
        cleaned = sanitize_reply_text(raw, max_questions=1)
        self.assertEqual(cleaned.count("？") + cleaned.count("?"), 1)

    def test_remove_english_placeholders(self) -> None:
        raw = "Hi [Recipient's Name],\nAcknowledged.\nBest regards,\n[Your Name]\n[Your Position]\n[Your Company]"
        cleaned = sanitize_reply_text(raw)
        self.assertNotIn("[Recipient's Name]", cleaned)
        self.assertNotIn("[Your Name]", cleaned)
        self.assertNotIn("[Your Position]", cleaned)
        self.assertNotIn("[Your Company]", cleaned)
        self.assertIn("Acknowledged.", cleaned)

    def test_remove_subject_from_to_lines(self) -> None:
        raw = (
            "Subject: Re: test\n"
            "From: bot@qq.com\n"
            "To: user@example.com\n"
            "Dear Sir/Madam,\n"
            "Thanks for your mail."
        )
        cleaned = sanitize_reply_text(raw)
        self.assertNotIn("Subject:", cleaned)
        self.assertNotIn("From:", cleaned)
        self.assertNotIn("To:", cleaned)
        self.assertNotIn("Dear Sir/Madam", cleaned)
        self.assertIn("Thanks for your mail.", cleaned)

    def test_postprocess_remove_placeholder_signature_zh(self) -> None:
        raw = "感谢来信。\n[您的姓名]\n[您的职位]\n[您的公司]"
        cleaned = sanitize_reply_text(raw)
        self.assertEqual(cleaned, "感谢来信。")


if __name__ == "__main__":
    unittest.main()
