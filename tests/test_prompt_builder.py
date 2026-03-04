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
        )
        pos_persona = prompt.find("Persona\n-------")
        pos_style = prompt.find("Style Rules\n-------")
        pos_exp = prompt.find("Relevant Experience\n-------")
        pos_examples = prompt.find("Examples\n-------")
        pos_disclosure = prompt.find("Disclosure Rule\n-------")
        pos_email = prompt.find("Email\n-------")

        self.assertTrue(
            pos_persona < pos_style < pos_exp < pos_examples < pos_disclosure < pos_email,
            msg=prompt,
        )

    def test_prompt_without_disclosure_does_not_include_profile(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="请看架构",
            body="我们讨论系统设计",
            bundle=self.bundle,
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
        )
        self.assertIn("Allowed profile context", prompt)
        self.assertIn('"role": "研究工程师"', prompt)

    def test_prompt_uses_fixed_first_examples(self) -> None:
        prompt = build_personalized_prompt(
            sender="alice@example.com",
            subject="agent",
            body="memory",
            bundle=self.bundle,
            example_top_k=3,
        )
        self.assertIn("Example 1 Q: q1", prompt)
        self.assertIn("Example 2 Q: q2", prompt)
        self.assertIn("Example 3 Q: q3", prompt)
        self.assertNotIn("Example 4 Q: q4", prompt)


if __name__ == "__main__":
    unittest.main()
