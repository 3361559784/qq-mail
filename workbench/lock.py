from __future__ import annotations

import json
import time
import uuid

from workbench.db import WorkbenchDB


LOCK_KEY = "sync_lock_owner"


class SyncLock:
    def __init__(self, db: WorkbenchDB, ttl_seconds: int = 600, owner: str | None = None) -> None:
        self.db = db
        self.ttl_seconds = ttl_seconds
        self.owner = owner or f"lock-{uuid.uuid4().hex[:12]}"
        self.acquired = False

    def try_acquire(self) -> bool:
        now = int(time.time())
        expires = now + self.ttl_seconds
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (LOCK_KEY,)).fetchone()
            if row is None:
                payload = {"owner": self.owner, "expires_at": expires}
                conn.execute(
                    "INSERT INTO sync_state(key, value) VALUES (?, ?)",
                    (LOCK_KEY, json.dumps(payload, ensure_ascii=False)),
                )
                conn.commit()
                self.acquired = True
                return True

            try:
                payload = json.loads(str(row["value"]))
            except Exception:
                payload = {}

            lock_owner = str(payload.get("owner", ""))
            lock_expires = int(payload.get("expires_at", 0))

            if lock_owner == self.owner or lock_expires <= now:
                next_payload = {"owner": self.owner, "expires_at": expires}
                conn.execute(
                    "UPDATE sync_state SET value = ? WHERE key = ?",
                    (json.dumps(next_payload, ensure_ascii=False), LOCK_KEY),
                )
                conn.commit()
                self.acquired = True
                return True

            conn.rollback()
            return False

    def release(self) -> None:
        if not self.acquired:
            return
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (LOCK_KEY,)).fetchone()
            if row is None:
                conn.rollback()
                self.acquired = False
                return
            try:
                payload = json.loads(str(row["value"]))
            except Exception:
                payload = {}
            if str(payload.get("owner", "")) == self.owner:
                conn.execute("DELETE FROM sync_state WHERE key = ?", (LOCK_KEY,))
                conn.commit()
            else:
                conn.rollback()
        self.acquired = False

    def __enter__(self) -> "SyncLock":
        self.try_acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.release()
