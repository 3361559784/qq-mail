from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from workbench.db import WorkbenchDB
from workbench.embed_store import EmbeddingClient, rebuild_faiss_from_sqlite, upsert_embedding_for_mail
from workbench.models import MailRecord
from workbench.search import SearchService


class TestWorkbenchSearch(unittest.TestCase):
    def test_search_returns_answer_and_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkbenchDB(Path(tmp) / "w.db")
            db.init_schema()
            for i, body in enumerate(["微软比赛报名截止日期是周五", "GitHub PR review requested"], start=1):
                mail_id = db.upsert_mail(
                    MailRecord(
                        message_id=f"<id-{i}>",
                        thread_key="t",
                        sender_email="a@example.com",
                        sender_name="A",
                        to_emails=[],
                        cc_emails=[],
                        subject=f"s{i}",
                        received_at_utc="2026-03-05T00:00:00Z",
                        body_text=body,
                        body_html="",
                        headers_json="{}",
                        flags_json="[]",
                        ingested_at_utc="2026-03-05T00:00:00Z",
                    )
                )
                row = db.get_mail_row(mail_id)
                assert row is not None
                client = EmbeddingClient(token="", api_url="", model="openai/text-embedding-3-small")
                upsert_embedding_for_mail(db=db, client=client, mail_row=row)

            rebuild_faiss_from_sqlite(
                db=db,
                model="openai/text-embedding-3-small",
                index_path=Path(tmp) / "faiss.index",
            )

            service = SearchService(
                db=db,
                embedding_client=EmbeddingClient(token="", api_url="", model="openai/text-embedding-3-small"),
                index_path=Path(tmp) / "faiss.index",
                llm_token="",
                llm_api_url="",
                llm_model="openai/gpt-4o-mini",
            )
            result = service.answer_with_evidence(query="比赛截止日期是什么", top_k=2)
            self.assertTrue(result.answer)
            self.assertGreaterEqual(len(result.hits), 1)


if __name__ == "__main__":
    unittest.main()
