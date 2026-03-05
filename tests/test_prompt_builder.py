from __future__ import annotations

import unittest

from personalization import PersonalizationBundle, build_personalized_prompt


class TestPromptBuilder(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = PersonalizationBundle(
            persona_text="Persona content",
            profile={"name": "刘梓恒", "role": "研究工程师"},
            projects=[
                {
                    "name": "QQ mail auto reply",
                    "stack": ["Azure Functions", "GitHub Models"],
                    "architecture": ["timer trigger worker", "email filtering"],
                    "keywords": ["email", "qq"],
                }
            ],
            preferences={
                "tone": ["professional", "polite"],
                "response_flow": ["identify problem", "give implementation"],
                "avoid": ["fluff"],
                "prefer": ["concrete steps"],
            },
            examples=[
                {"question": "q1", "answer": "a1"},
                {"question": "q2", "answer": "a2"},
                {"question": "q3", "answer": "a3"},
                {"question": "q4", "answer": "a4"},
            ],
            notes_text="Prefer minimal architecture.",
        )

    def test_prompt_order_is_fixed(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="Azure Functions 邮件机器人",
            body="请给我一个实现建议",
            bundle=self.bundle,
            language="zh",
        )
        pos_persona = prompt.find("Persona\n-------")
        pos_reasoning = prompt.find("Reasoning Flow\n-------")
        pos_pref = prompt.find("Engineering Preferences\n-------")
        pos_tone = prompt.find("Tone Constraints\n-------")
        pos_exp = prompt.find("Relevant Experience\n-------")
        pos_examples = prompt.find("Fixed Examples\n-------")
        pos_disclosure = prompt.find("Disclosure Rule\n-------")
        pos_contract = prompt.find("Output Contract\n-------")
        pos_email = prompt.find("Incoming Email\n-------")

        self.assertTrue(
            pos_persona
            < pos_reasoning
            < pos_pref
            < pos_tone
            < pos_exp
            < pos_examples
            < pos_disclosure
            < pos_contract
            < pos_email,
            msg=prompt,
        )

    def test_prompt_contains_reasoning_flow_and_output_contract(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="How to design mail automation?",
            body="Please provide a practical plan.",
            bundle=self.bundle,
            language="en",
        )
        self.assertIn("Reasoning Flow\n-------", prompt)
        self.assertIn("Output Contract\n-------", prompt)
        self.assertIn("Output body text only; do not output Subject:/From:/To: lines.", prompt)

    def test_prompt_disclosure_off_by_default(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="请看架构",
            body="我们讨论系统设计",
            bundle=self.bundle,
            language="en",
        )
        self.assertIn("do NOT mention personal background", prompt)
        self.assertNotIn("Allowed profile context", prompt)
        self.assertNotIn('"role": "研究工程师"', prompt)

    def test_prompt_with_disclosure_includes_profile(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="你是谁",
            body="请介绍一下你自己",
            bundle=self.bundle,
            language="en",
        )
        self.assertIn("Allowed profile context", prompt)
        self.assertIn('"role": "研究工程师"', prompt)

    def test_prompt_uses_fixed_first_examples(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="agent",
            body="memory",
            bundle=self.bundle,
            language="en",
            example_top_k=3,
        )
        self.assertIn("Example 1 Q: q1", prompt)
        self.assertIn("Example 2 Q: q2", prompt)
        self.assertIn("Example 3 Q: q3", prompt)
        self.assertNotIn("Example 4 Q: q4", prompt)

    def test_prompt_language_policy_zh(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="请帮我看架构",
            body="我们要做一个自动化 worker。",
            bundle=self.bundle,
            language="zh",
        )
        self.assertIn("你正在以刘梓恒的身份回复邮件", prompt)
        self.assertIn("仅输出邮件正文，不要输出 Subject:/From:/To:。", prompt)

    def test_prompt_language_policy_en(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="Please review architecture",
            body="Need concise implementation guidance.",
            bundle=self.bundle,
            language="en",
        )
        self.assertIn("You are replying to an email as Liu Ziheng.", prompt)
        self.assertIn("Output body text only; do not output Subject:/From:/To: lines.", prompt)


if __name__ == "__main__":
    unittest.main()
