from __future__ import annotations

import tempfile
from email.message import EmailMessage
from pathlib import Path
import unittest

from workbench.attachments import download_attachments
from workbench.db import WorkbenchDB
from workbench.models import MailRecord


class TestWorkbenchAttachments(unittest.TestCase):
    def _insert_mail(self, db: WorkbenchDB, message_id: str) -> int:
        return db.upsert_mail(
            MailRecord(
                message_id=message_id,
                thread_key="k",
                sender_email="a@example.com",
                sender_name="a",
                to_emails=[],
                cc_emails=[],
                subject="s",
                received_at_utc="2026-03-05T00:00:00Z",
                body_text="b",
                body_html="",
                headers_json="{}",
                flags_json="[]",
                ingested_at_utc="2026-03-05T00:00:00Z",
            )
        )

    def test_attachment_skip_over_20mb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkbenchDB(Path(tmp) / "w.db")
            db.init_schema()
            mail_id = self._insert_mail(db, "<id-1>")

            msg = EmailMessage()
            msg.set_content("hello")
            msg.add_attachment(b"A" * (21 * 1024 * 1024), maintype="application", subtype="octet-stream", filename="big.bin")

            stats = download_attachments(db=db, msg=msg, mail_id=mail_id, attach_root=Path(tmp) / "att", max_mb=20)
            self.assertEqual(stats.downloaded, 0)
            self.assertEqual(stats.skipped, 1)

    def test_attachment_sha256_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkbenchDB(Path(tmp) / "w.db")
            db.init_schema()
            root = Path(tmp) / "att"

            mail1 = self._insert_mail(db, "<id-a>")
            msg1 = EmailMessage()
            msg1.set_content("x")
            msg1.add_attachment(b"same-payload", maintype="application", subtype="octet-stream", filename="a.bin")
            stats1 = download_attachments(db=db, msg=msg1, mail_id=mail1, attach_root=root, max_mb=20)
            self.assertEqual(stats1.downloaded, 1)

            mail2 = self._insert_mail(db, "<id-b>")
            msg2 = EmailMessage()
            msg2.set_content("y")
            msg2.add_attachment(b"same-payload", maintype="application", subtype="octet-stream", filename="b.bin")
            stats2 = download_attachments(db=db, msg=msg2, mail_id=mail2, attach_root=root, max_mb=20)
            self.assertEqual(stats2.downloaded, 1)

            with db.session() as conn:
                rows = conn.execute("SELECT local_path FROM attachments WHERE download_status='downloaded' ORDER BY id").fetchall()
            self.assertEqual(rows[0]["local_path"], rows[1]["local_path"])


if __name__ == "__main__":
    unittest.main()
