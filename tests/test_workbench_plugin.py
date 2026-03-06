from __future__ import annotations

import unittest

from workbench.plugins.github_notifications import create_github_tasks, extract_github_entities, match_github_notification


class TestWorkbenchPlugin(unittest.TestCase):
    def test_github_plugin_extracts_repo_issue_pr(self) -> None:
        self.assertTrue(
            match_github_notification(
                sender_email="notifications@github.com",
                subject="Review requested on owner/repo#12",
                headers_json="{}",
            )
        )
        entity = extract_github_entities(
            subject="owner/repo pull request #12 review requested",
            body_text="https://github.com/owner/repo/pull/12",
        )
        self.assertIsNotNone(entity)
        assert entity is not None
        tasks = create_github_tasks(entity)
        self.assertEqual(len(tasks), 1)
        self.assertIn("owner/repo", tasks[0].title)


if __name__ == "__main__":
    unittest.main()
