from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from workbench.db import WorkbenchDB
from workbench.embed_store import EmbeddingClient, rebuild_faiss_from_sqlite, upsert_embedding_for_mail
from workbench.models import MailRecord


class TestWorkbenchEmbedStore(unittest.TestCase):
    def _db_with_mail(self):
        tmp = tempfile.TemporaryDirectory()
        db = WorkbenchDB(Path(tmp.name) / "w.db")
        db.init_schema()
        mail_id = db.upsert_mail(
            MailRecord(
                message_id="<id-1>",
                thread_key="t",
                sender_email="a@example.com",
                sender_name="A",
                to_emails=[],
                cc_emails=[],
                subject="Subject",
                received_at_utc="2026-03-05T00:00:00Z",
                body_text="Body content",
                body_html="",
                headers_json="{}",
                flags_json="[]",
                ingested_at_utc="2026-03-05T00:00:00Z",
            )
        )
        row = db.get_mail_row(mail_id)
        assert row is not None
        return tmp, db, row

    def test_embedding_cache_by_content_hash(self) -> None:
        tmp, db, row = self._db_with_mail()
        try:
            client = EmbeddingClient(token="", api_url="", model="openai/text-embedding-3-small")
            first = upsert_embedding_for_mail(db=db, client=client, mail_row=row)
            second = upsert_embedding_for_mail(db=db, client=client, mail_row=row)
            self.assertTrue(first)
            self.assertFalse(second)
        finally:
            tmp.cleanup()

    def test_faiss_pos_mapping_consistent(self) -> None:
        tmp, db, row = self._db_with_mail()
        try:
            client = EmbeddingClient(token="", api_url="", model="openai/text-embedding-3-small")
            upsert_embedding_for_mail(db=db, client=client, mail_row=row)
            count = rebuild_faiss_from_sqlite(
                db=db,
                model="openai/text-embedding-3-small",
                index_path=Path(tmp.name) / "faiss.index",
            )
            self.assertEqual(count, 1)
            rows = db.list_embeddings(model="openai/text-embedding-3-small")
            self.assertEqual(int(rows[0]["faiss_pos"]), 0)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
