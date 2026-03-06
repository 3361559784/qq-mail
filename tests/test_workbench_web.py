from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from workbench.db import WorkbenchDB
from workbench.models import FinalDecision, MailRecord, TaskDraft
from workbench.web_app import create_workbench_app


class TestWorkbenchWeb(unittest.TestCase):
    def _make_settings(self, tmp: str):
        return SimpleNamespace(
            workbench_db_path=Path(tmp) / "w.db",
            github_token="",
            github_embedding_api_url="",
            workbench_embed_model="openai/text-embedding-3-small",
            workbench_faiss_index_path=Path(tmp) / "faiss.index",
            github_api_url="",
            workbench_llm_model="openai/gpt-4o-mini",
            workbench_vector_top_k=5,
            workbench_read_only=True,
            workbench_sync_interval_seconds=300,
            model_request_timeout_seconds=10,
        )

    def test_dashboard_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            db = WorkbenchDB(settings.workbench_db_path)
            db.init_schema()
            mail_id = db.upsert_mail(
                MailRecord(
                    message_id="<id-1>",
                    thread_key="k",
                    sender_email="a@example.com",
                    sender_name="A",
                    to_emails=[],
                    cc_emails=[],
                    subject="subject",
                    received_at_utc="2026-03-05T00:00:00Z",
                    body_text="body",
                    body_html="",
                    headers_json="{}",
                    flags_json="[]",
                    ingested_at_utc="2026-03-05T00:00:00Z",
                )
            )
            db.upsert_triage(
                mail_id=mail_id,
                decision=FinalDecision(
                    category="action",
                    priority="high",
                    needs_action=True,
                    evidence=["x"],
                    confidence=0.8,
                    strategy="rules_only",
                    model_name="rules",
                ),
                triaged_at_utc="2026-03-05T00:00:00Z",
            )
            db.insert_tasks(
                mail_id=mail_id,
                tasks=[TaskDraft(title="do", priority="high", due_at_utc=None, evidence="x", source="rule")],
                created_at_utc="2026-03-05T00:00:00Z",
            )

            app = create_workbench_app(settings=settings, enable_scheduler=False)
            client = TestClient(app)
            r = client.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Action", r.text)

    def test_tasks_mark_done_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            db = WorkbenchDB(settings.workbench_db_path)
            db.init_schema()
            mail_id = db.upsert_mail(
                MailRecord(
                    message_id="<id-2>",
                    thread_key="k",
                    sender_email="a@example.com",
                    sender_name="A",
                    to_emails=[],
                    cc_emails=[],
                    subject="subject",
                    received_at_utc="2026-03-05T00:00:00Z",
                    body_text="body",
                    body_html="",
                    headers_json="{}",
                    flags_json="[]",
                    ingested_at_utc="2026-03-05T00:00:00Z",
                )
            )
            db.insert_tasks(
                mail_id=mail_id,
                tasks=[TaskDraft(title="done me", priority="med", due_at_utc=None, evidence="e", source="rule")],
                created_at_utc="2026-03-05T00:00:00Z",
            )
            rows = db.list_tasks(status="open")
            task_id = int(rows[0]["id"])

            app = create_workbench_app(settings=settings, enable_scheduler=False)
            client = TestClient(app)
            r = client.post(f"/tasks/{task_id}/done", follow_redirects=False)
            self.assertEqual(r.status_code, 303)

    def test_search_endpoint_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(tmp)
            db = WorkbenchDB(settings.workbench_db_path)
            db.init_schema()
            app = create_workbench_app(settings=settings, enable_scheduler=False)
            client = TestClient(app)
            r = client.get("/search?q=test")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Vector Search", r.text)


if __name__ == "__main__":
    unittest.main()
