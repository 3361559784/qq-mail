from __future__ import annotations

import unittest

from personalization import build_output_contract, detect_reply_language


class TestLanguagePolicy(unittest.TestCase):
    def test_detect_reply_language_prefers_subject_zh(self) -> None:
        language = detect_reply_language(
            subject="请看这个架构问题",
            body="Can you check this implementation detail?",
        )
        self.assertEqual(language, "zh")

    def test_detect_reply_language_prefers_subject_en(self) -> None:
        language = detect_reply_language(
            subject="Please review this design",
            body="这里有一些中文内容",
        )
        self.assertEqual(language, "en")

    def test_detect_reply_language_uses_body_when_subject_neutral(self) -> None:
        language = detect_reply_language(
            subject="12345",
            body="你好，我想确认这个实现是否可行。",
        )
        self.assertEqual(language, "zh")

    def test_detect_reply_language_defaults_en(self) -> None:
        language = detect_reply_language(
            subject="12345",
            body="--",
        )
        self.assertEqual(language, "en")

    def test_build_output_contract_zh(self) -> None:
        contract = build_output_contract(language="zh", style_mode="polite")
        self.assertIn("仅输出邮件正文", contract)
        self.assertIn("禁止占位符", contract)
        self.assertIn("最多允许 1 个澄清问题", contract)

    def test_build_output_contract_en(self) -> None:
        contract = build_output_contract(language="en", style_mode="polite")
        self.assertIn("Output body text only", contract)
        self.assertIn("Do not use placeholders", contract)
        self.assertIn("at most one", contract.lower())


if __name__ == "__main__":
    unittest.main()
