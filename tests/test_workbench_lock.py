from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from workbench.db import WorkbenchDB
from workbench.lock import SyncLock


class TestWorkbenchLock(unittest.TestCase):
    def test_sync_lock_single_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkbenchDB(Path(tmp) / "w.db")
            db.init_schema()

            a = SyncLock(db=db, ttl_seconds=60, owner="A")
            b = SyncLock(db=db, ttl_seconds=60, owner="B")

            self.assertTrue(a.try_acquire())
            self.assertFalse(b.try_acquire())

            a.release()
            self.assertTrue(b.try_acquire())
            b.release()


if __name__ == "__main__":
    unittest.main()
