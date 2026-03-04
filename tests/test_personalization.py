from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from personalization import (
    load_personalization_bundle,
    needs_profile_disclosure,
    select_fixed_examples,
    select_relevant_memories,
)


class TestPersonalization(unittest.TestCase):
    def _write_bundle_files(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "persona.md").write_text("persona", encoding="utf-8")
        (root / "notes.md").write_text("note", encoding="utf-8")
        (root / "profile.json").write_text(json.dumps({"name": "刘梓恒"}, ensure_ascii=False), encoding="utf-8")
        (root / "projects.json").write_text(
            json.dumps(
                [
                    {
                        "name": "QQ mail auto reply",
                        "stack": ["Azure Functions", "GitHub Models"],
                        "architecture": ["timer trigger worker", "email filtering"],
                        "keywords": ["email", "qq mail"],
                    },
                    {
                        "name": "Other project",
                        "stack": ["Django"],
                        "architecture": ["web"],
                        "keywords": ["frontend"],
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "preferences.json").write_text(
            json.dumps(
                {
                    "tone": ["professional"],
                    "response_flow": ["identify problem"],
                    "avoid": ["fluff"],
                    "prefer": ["concrete steps"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "qa_examples.json").write_text(
            json.dumps(
                [
                    {"question": "q1", "answer": "a1"},
                    {"question": "q2", "answer": "a2"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_load_bundle_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "personalization"
            self._write_bundle_files(root)
            bundle = load_personalization_bundle(Path(tmp))
            self.assertEqual(bundle.persona_text, "persona")
            self.assertEqual(bundle.profile["name"], "刘梓恒")
            self.assertEqual(len(bundle.projects), 2)
            self.assertEqual(len(bundle.examples), 2)

    def test_select_relevant_memories_overlap_scoring(self) -> None:
        projects = [
            {
                "name": "QQ mail auto reply",
                "stack": ["Azure Functions", "GitHub Models"],
                "architecture": ["timer trigger worker", "email filtering"],
                "keywords": ["email bot"],
            },
            {
                "name": "Unrelated",
                "stack": ["Django"],
                "architecture": ["web template"],
                "keywords": ["frontend"],
            },
        ]
        result = select_relevant_memories(
            subject="Azure Functions 邮件机器人",
            body="想优化 email filtering 逻辑",
            projects=projects,
            top_k=3,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "QQ mail auto reply")

    def test_select_relevant_memories_returns_top_k_score_gt_zero(self) -> None:
        projects = [
            {"name": "A", "stack": ["Python"], "architecture": ["worker"], "keywords": ["cron"]},
            {"name": "B", "stack": ["Azure"], "architecture": ["function"], "keywords": ["mail"]},
            {"name": "C", "stack": ["Azure"], "architecture": ["timer"], "keywords": ["bot"]},
        ]
        result = select_relevant_memories(
            subject="Azure mail",
            body="",
            projects=projects,
            top_k=2,
        )
        self.assertLessEqual(len(result), 2)
        self.assertTrue(all(isinstance(item, dict) for item in result))

    def test_select_fixed_examples_deterministic(self) -> None:
        examples = [
            {"question": "q1", "answer": "a1"},
            {"question": "q2", "answer": "a2"},
            {"question": "q3", "answer": "a3"},
            {"question": "q4", "answer": "a4"},
        ]
        selected = select_fixed_examples(examples, k=3)
        self.assertEqual(selected, examples[:3])

    def test_needs_profile_disclosure_true(self) -> None:
        self.assertTrue(needs_profile_disclosure("你是谁", "请介绍一下你自己"))

    def test_needs_profile_disclosure_false(self) -> None:
        self.assertFalse(needs_profile_disclosure("帮我看下系统架构", "不涉及个人介绍"))


if __name__ == "__main__":
    unittest.main()
