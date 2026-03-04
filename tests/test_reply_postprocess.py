from __future__ import annotations

import unittest

from mail_client import sanitize_reply_text


class TestReplyPostprocess(unittest.TestCase):
    def test_postprocess_remove_redundant_closing(self) -> None:
        raw = (
            "您好，\n"
            "链路已收到，我会继续跟进。\n"
            "祝好！\n"
            "祝好，\n"
            "此致\n"
            "敬礼"
        )
        cleaned = sanitize_reply_text(raw)
        self.assertEqual(cleaned, "您好，\n链路已收到，我会继续跟进。")

    def test_postprocess_keep_single_clarifying_question(self) -> None:
        raw = "你好，请问你的目标是什么？你希望我先检查模型还是邮件投递？还需要我给你测试清单吗？"
        cleaned = sanitize_reply_text(raw, max_questions=1)
        self.assertEqual(cleaned.count("？") + cleaned.count("?"), 1)

    def test_postprocess_remove_placeholder_signature(self) -> None:
        raw = "感谢来信。\n[您的姓名]\n[您的职位]\n[您的公司]"
        cleaned = sanitize_reply_text(raw)
        self.assertEqual(cleaned, "感谢来信。")


if __name__ == "__main__":
    unittest.main()
