from __future__ import annotations

import unittest

from mail_client import compose_reply_body


class TestReplyFormat(unittest.TestCase):
    def test_last_two_lines_model_signature(self) -> None:
        used_model = "openai/gpt-4.1"
        body = compose_reply_body(
            ai_text="这是正文",
            reply_signature="这是一封自动回复邮件。",
            model_signature_template="--\n使用 {model} 模型自动生成回复",
            used_model=used_model,
        )
        lines = body.splitlines()
        self.assertEqual(lines[-2], "--")
        self.assertEqual(lines[-1], f"使用 {used_model} 模型自动生成回复")

    def test_used_model_must_match_actual(self) -> None:
        used_model = "meta/llama-3.3-70b-instruct"
        body = compose_reply_body(
            ai_text="正文",
            reply_signature="",
            model_signature_template="--\n使用 {model} 模型自动生成回复",
            used_model=used_model,
        )
        self.assertIn(f"使用 {used_model} 模型自动生成回复", body)

    def test_order_with_reply_signature(self) -> None:
        body = compose_reply_body(
            ai_text="第一段正文",
            reply_signature="第二段签名",
            model_signature_template="--\n使用 {model} 模型自动生成回复",
            used_model="openai/gpt-4.1",
        )
        expected = "第一段正文\n\n第二段签名\n\n--\n使用 openai/gpt-4.1 模型自动生成回复"
        self.assertEqual(body, expected)

    def test_compose_order_still_valid_after_postprocess(self) -> None:
        body = compose_reply_body(
            ai_text="这是正文\n\n祝好，\n[您的姓名]",
            reply_signature="固定签名",
            model_signature_template="--\n使用 {model} 模型自动生成回复",
            used_model="openai/gpt-4o",
        )
        expected = "这是正文\n祝好，\n\n固定签名\n\n--\n使用 openai/gpt-4o 模型自动生成回复"
        self.assertEqual(body, expected)


if __name__ == "__main__":
    unittest.main()
