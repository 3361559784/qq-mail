from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class MailRecord:
    message_id: str
    thread_key: str
    sender_email: str
    sender_name: str
    to_emails: list[str]
    cc_emails: list[str]
    subject: str
    received_at_utc: str
    body_text: str
    body_html: str
    headers_json: str
    flags_json: str
    ingested_at_utc: str


@dataclass(frozen=True)
class AttachmentRecord:
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    local_path: str
    download_status: str
    error_msg: str = ""


@dataclass(frozen=True)
class RuleDecision:
    category: str
    priority: str
    needs_action: bool
    evidence: list[str]
    is_candidate: bool


@dataclass(frozen=True)
class LlmDecision:
    category: str
    priority: str
    needs_action: bool
    suggested_tasks: list[str]
    due_date_guess: str | None
    evidence: list[str]
    confidence: float
    model_name: str


@dataclass(frozen=True)
class FinalDecision:
    category: str
    priority: str
    needs_action: bool
    evidence: list[str]
    confidence: float
    strategy: str
    model_name: str
    suggested_tasks: list[str] = field(default_factory=list)
    due_date_guess: str | None = None


@dataclass(frozen=True)
class TaskDraft:
    title: str
    priority: str
    due_at_utc: str | None
    evidence: str
    source: str


@dataclass(frozen=True)
class SearchHit:
    mail_id: int
    subject: str
    sender: str
    received_at_utc: str
    snippet: str
    score: float


@dataclass(frozen=True)
class QaAnswer:
    answer: str
    hits: list[SearchHit]
    evidence: list[str]


@dataclass(frozen=True)
class SyncStats:
    fetched: int
    inserted_or_updated: int
    llm_called: int
    tasks_created: int
    attachments_downloaded: int
    attachments_skipped: int
    errors: int
    finished_at_utc: str


@dataclass(frozen=True)
class EmbeddingRecord:
    mail_id: int
    content_hash: str
    model: str
    vector_dim: int
    vector_blob: bytes
    faiss_pos: int
    updated_at_utc: str


@dataclass(frozen=True)
class GithubEntity:
    repo: str
    item_type: str
    item_number: str
    action: str
    url: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_json_dumps(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, sort_keys=True)
