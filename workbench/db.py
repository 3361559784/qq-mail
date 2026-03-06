from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Sequence

from workbench.models import AttachmentRecord, FinalDecision, MailRecord, TaskDraft, safe_json_dumps


class WorkbenchDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def session(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE NOT NULL,
                    thread_key TEXT NOT NULL,
                    sender_email TEXT,
                    sender_name TEXT,
                    to_json TEXT,
                    cc_json TEXT,
                    subject TEXT,
                    received_at_utc TEXT,
                    body_text TEXT,
                    body_html TEXT,
                    headers_json TEXT,
                    flags_json TEXT,
                    ingested_at_utc TEXT
                );

                CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mail_id INTEGER NOT NULL,
                    filename TEXT,
                    mime_type TEXT,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    local_path TEXT,
                    download_status TEXT,
                    error_msg TEXT,
                    FOREIGN KEY(mail_id) REFERENCES mails(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS triage (
                    mail_id INTEGER PRIMARY KEY,
                    category TEXT,
                    priority TEXT,
                    needs_action INTEGER,
                    evidence_json TEXT,
                    confidence REAL,
                    model_name TEXT,
                    strategy TEXT,
                    triaged_at_utc TEXT,
                    due_date_guess TEXT,
                    FOREIGN KEY(mail_id) REFERENCES mails(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mail_id INTEGER,
                    title TEXT,
                    status TEXT,
                    priority TEXT,
                    due_at_utc TEXT,
                    evidence TEXT,
                    source TEXT,
                    created_at_utc TEXT,
                    UNIQUE(mail_id, title),
                    FOREIGN KEY(mail_id) REFERENCES mails(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS embeddings (
                    mail_id INTEGER PRIMARY KEY,
                    content_hash TEXT,
                    model TEXT,
                    vector_dim INTEGER,
                    vector_blob BLOB,
                    faiss_pos INTEGER,
                    updated_at_utc TEXT,
                    FOREIGN KEY(mail_id) REFERENCES mails(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_mails_received_at ON mails(received_at_utc);
                CREATE INDEX IF NOT EXISTS idx_mails_sender ON mails(sender_email);
                CREATE INDEX IF NOT EXISTS idx_mails_thread ON mails(thread_key);
                CREATE INDEX IF NOT EXISTS idx_attachments_mail ON attachments(mail_id);
                CREATE INDEX IF NOT EXISTS idx_triage_category ON triage(category);
                CREATE INDEX IF NOT EXISTS idx_triage_priority ON triage(priority);
                CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at_utc);
                CREATE INDEX IF NOT EXISTS idx_tasks_mail ON tasks(mail_id);
                CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);
                """
            )

    def upsert_mail(self, record: MailRecord) -> int:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO mails (
                    message_id, thread_key, sender_email, sender_name, to_json, cc_json,
                    subject, received_at_utc, body_text, body_html, headers_json, flags_json, ingested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    thread_key=excluded.thread_key,
                    sender_email=excluded.sender_email,
                    sender_name=excluded.sender_name,
                    to_json=excluded.to_json,
                    cc_json=excluded.cc_json,
                    subject=excluded.subject,
                    received_at_utc=excluded.received_at_utc,
                    body_text=excluded.body_text,
                    body_html=excluded.body_html,
                    headers_json=excluded.headers_json,
                    flags_json=excluded.flags_json,
                    ingested_at_utc=excluded.ingested_at_utc
                """,
                (
                    record.message_id,
                    record.thread_key,
                    record.sender_email,
                    record.sender_name,
                    safe_json_dumps(record.to_emails),
                    safe_json_dumps(record.cc_emails),
                    record.subject,
                    record.received_at_utc,
                    record.body_text,
                    record.body_html,
                    record.headers_json,
                    record.flags_json,
                    record.ingested_at_utc,
                ),
            )
            row = conn.execute("SELECT id FROM mails WHERE message_id = ?", (record.message_id,)).fetchone()
            if row is None:
                raise RuntimeError("Failed to load upserted mail id")
            return int(row["id"])

    def get_mail_row(self, mail_id: int) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()

    def get_mail_by_message_id(self, message_id: str) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute("SELECT * FROM mails WHERE message_id = ?", (message_id,)).fetchone()

    def insert_attachment(self, mail_id: int, record: AttachmentRecord) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO attachments(mail_id, filename, mime_type, size_bytes, sha256, local_path, download_status, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mail_id,
                    record.filename,
                    record.mime_type,
                    record.size_bytes,
                    record.sha256,
                    record.local_path,
                    record.download_status,
                    record.error_msg,
                ),
            )

    def find_downloaded_attachment_path_by_sha(self, sha256: str) -> str | None:
        if not sha256:
            return None
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT local_path FROM attachments
                WHERE sha256 = ? AND download_status = 'downloaded' AND local_path != ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (sha256,),
            ).fetchone()
            return str(row["local_path"]) if row else None

    def upsert_triage(self, mail_id: int, decision: FinalDecision, triaged_at_utc: str) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO triage(mail_id, category, priority, needs_action, evidence_json, confidence, model_name, strategy, triaged_at_utc, due_date_guess)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mail_id) DO UPDATE SET
                    category=excluded.category,
                    priority=excluded.priority,
                    needs_action=excluded.needs_action,
                    evidence_json=excluded.evidence_json,
                    confidence=excluded.confidence,
                    model_name=excluded.model_name,
                    strategy=excluded.strategy,
                    triaged_at_utc=excluded.triaged_at_utc,
                    due_date_guess=excluded.due_date_guess
                """,
                (
                    mail_id,
                    decision.category,
                    decision.priority,
                    1 if decision.needs_action else 0,
                    safe_json_dumps(decision.evidence),
                    float(decision.confidence),
                    decision.model_name,
                    decision.strategy,
                    triaged_at_utc,
                    decision.due_date_guess,
                ),
            )

    def insert_tasks(self, mail_id: int, tasks: Sequence[TaskDraft], created_at_utc: str) -> int:
        if not tasks:
            return 0
        with self.session() as conn:
            created = 0
            for task in tasks:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO tasks(mail_id, title, status, priority, due_at_utc, evidence, source, created_at_utc)
                    VALUES (?, ?, 'open', ?, ?, ?, ?, ?)
                    """,
                    (
                        mail_id,
                        task.title,
                        task.priority,
                        task.due_at_utc,
                        task.evidence,
                        task.source,
                        created_at_utc,
                    ),
                )
                if cur.rowcount:
                    created += 1
            return created

    def list_tasks(self, status: str = "open") -> list[sqlite3.Row]:
        with self.session() as conn:
            return list(
                conn.execute(
                    """
                    SELECT t.*, m.subject, m.sender_email
                    FROM tasks t
                    LEFT JOIN mails m ON m.id = t.mail_id
                    WHERE t.status = ?
                    ORDER BY COALESCE(t.due_at_utc, '9999-12-31T23:59:59Z'), t.id DESC
                    """,
                    (status,),
                ).fetchall()
            )

    def mark_task_done(self, task_id: int) -> bool:
        with self.session() as conn:
            cur = conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
            return cur.rowcount > 0

    def count_by_category(self) -> dict[str, int]:
        with self.session() as conn:
            rows = conn.execute("SELECT category, COUNT(*) AS c FROM triage GROUP BY category").fetchall()
        result = {"action": 0, "waiting": 0, "fyi": 0, "spamish": 0}
        for row in rows:
            result[str(row["category"])] = int(row["c"])
        return result

    def list_mails(self, category: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        query = (
            """
            SELECT m.*, t.category, t.priority, t.needs_action, t.confidence, t.strategy
            FROM mails m
            LEFT JOIN triage t ON t.mail_id = m.id
            """
        )
        params: list[object] = []
        if category:
            query += " WHERE t.category = ?"
            params.append(category)
        query += " ORDER BY m.received_at_utc DESC LIMIT ?"
        params.append(limit)

        with self.session() as conn:
            return list(conn.execute(query, params).fetchall())

    def get_mail_detail(self, mail_id: int) -> dict[str, object] | None:
        with self.session() as conn:
            mail = conn.execute(
                """
                SELECT m.*, t.category, t.priority, t.needs_action, t.evidence_json, t.strategy, t.model_name, t.confidence, t.due_date_guess
                FROM mails m
                LEFT JOIN triage t ON t.mail_id = m.id
                WHERE m.id = ?
                """,
                (mail_id,),
            ).fetchone()
            if mail is None:
                return None
            attachments = list(conn.execute("SELECT * FROM attachments WHERE mail_id = ? ORDER BY id", (mail_id,)).fetchall())
            tasks = list(conn.execute("SELECT * FROM tasks WHERE mail_id = ? ORDER BY id DESC", (mail_id,)).fetchall())
            return {"mail": mail, "attachments": attachments, "tasks": tasks}

    def upsert_embedding(
        self,
        mail_id: int,
        content_hash: str,
        model: str,
        vector: bytes,
        vector_dim: int,
        faiss_pos: int,
        updated_at_utc: str,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO embeddings(mail_id, content_hash, model, vector_blob, vector_dim, faiss_pos, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mail_id) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    model=excluded.model,
                    vector_blob=excluded.vector_blob,
                    vector_dim=excluded.vector_dim,
                    faiss_pos=excluded.faiss_pos,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (mail_id, content_hash, model, vector, vector_dim, faiss_pos, updated_at_utc),
            )

    def get_embedding_meta(self, mail_id: int) -> sqlite3.Row | None:
        with self.session() as conn:
            return conn.execute(
                "SELECT mail_id, content_hash, model, vector_dim, faiss_pos, updated_at_utc FROM embeddings WHERE mail_id = ?",
                (mail_id,),
            ).fetchone()

    def list_embeddings(self, model: str) -> list[sqlite3.Row]:
        with self.session() as conn:
            return list(
                conn.execute(
                    "SELECT mail_id, content_hash, model, vector_blob, vector_dim, faiss_pos, updated_at_utc FROM embeddings WHERE model = ? ORDER BY COALESCE(faiss_pos, 999999), mail_id",
                    (model,),
                ).fetchall()
            )

    def update_faiss_positions(self, model: str, positions: Iterable[tuple[int, int]]) -> None:
        with self.session() as conn:
            for mail_id, pos in positions:
                conn.execute(
                    "UPDATE embeddings SET faiss_pos = ? WHERE mail_id = ? AND model = ?",
                    (pos, mail_id, model),
                )

    def get_mails_by_ids(self, ids: Sequence[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        placeholders = ",".join(["?"] * len(ids))
        with self.session() as conn:
            rows = conn.execute(
                f"SELECT id, subject, sender_email, received_at_utc, body_text FROM mails WHERE id IN ({placeholders})",
                list(ids),
            ).fetchall()
        return {int(row["id"]): row for row in rows}

    def set_state(self, key: str, value: str) -> None:
        with self.session() as conn:
            conn.execute(
                "INSERT INTO sync_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_state(self, key: str, default: str = "") -> str:
        with self.session() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def delete_state(self, key: str) -> None:
        with self.session() as conn:
            conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))

    def clear_tables_for_test(self) -> None:
        with self.session() as conn:
            conn.executescript(
                """
                DELETE FROM attachments;
                DELETE FROM tasks;
                DELETE FROM triage;
                DELETE FROM embeddings;
                DELETE FROM mails;
                DELETE FROM sync_state;
                """
            )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, object]:
    if row is None:
        return {}
    return {k: row[k] for k in row.keys()}
