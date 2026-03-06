from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from dataclasses import replace

from workbench.db import WorkbenchDB
from workbench.models import MailRecord


class TestWorkbenchDB(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = WorkbenchDB(Path(self.tmp.name) / "workbench.db")
        self.db.init_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _sample_mail(self, message_id: str) -> MailRecord:
        return MailRecord(
            message_id=message_id,
            thread_key="abc123",
            sender_email="a@example.com",
            sender_name="A",
            to_emails=["b@example.com"],
            cc_emails=[],
            subject="test",
            received_at_utc="2026-03-05T00:00:00Z",
            body_text="hello",
            body_html="",
            headers_json="{}",
            flags_json="[]",
            ingested_at_utc="2026-03-05T00:00:01Z",
        )

    def test_db_schema_init(self) -> None:
        with self.db.session() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {row["name"] for row in rows}
        self.assertIn("mails", names)
        self.assertIn("attachments", names)
        self.assertIn("sync_state", names)

    def test_message_id_unique_upsert(self) -> None:
        mail = self._sample_mail("<id-1>")
        id1 = self.db.upsert_mail(mail)
        id2 = self.db.upsert_mail(mail)
        self.assertEqual(id1, id2)

        with self.db.session() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM mails WHERE message_id = ?", ("<id-1>",)).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_flags_json_persisted(self) -> None:
        mail = replace(self._sample_mail("<id-flags>"), flags_json='["\\\\Seen","\\\\Flagged"]')
        mail_id = self.db.upsert_mail(mail)
        row = self.db.get_mail_row(mail_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["flags_json"], '["\\\\Seen","\\\\Flagged"]')


if __name__ == "__main__":
    unittest.main()
