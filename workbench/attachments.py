from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from mail_client import decode_mime
from workbench.db import WorkbenchDB
from workbench.models import AttachmentRecord


@dataclass(frozen=True)
class AttachmentStats:
    downloaded: int
    skipped: int


def _safe_filename(name: str) -> str:
    cleaned = decode_mime(name, "attachment.bin")
    cleaned = cleaned.replace("/", "_").replace("\\", "_")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "attachment.bin"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    idx = 1
    while True:
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1


def download_attachments(
    db: WorkbenchDB,
    msg: EmailMessage,
    mail_id: int,
    attach_root: Path,
    max_mb: int,
) -> AttachmentStats:
    max_bytes = max(max_mb, 1) * 1024 * 1024
    downloaded = 0
    skipped = 0

    mail_dir = attach_root / str(mail_id)
    mail_dir.mkdir(parents=True, exist_ok=True)

    for part in msg.walk():
        content_disposition = (part.get_content_disposition() or "").lower()
        filename_raw = part.get_filename()
        if content_disposition != "attachment" and not filename_raw:
            continue

        filename = _safe_filename(filename_raw or "attachment.bin")
        mime_type = part.get_content_type() or "application/octet-stream"

        try:
            payload = part.get_payload(decode=True) or b""
        except Exception as exc:
            db.insert_attachment(
                mail_id,
                AttachmentRecord(
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=0,
                    sha256="",
                    local_path="",
                    download_status="failed",
                    error_msg=str(exc),
                ),
            )
            skipped += 1
            continue

        size_bytes = len(payload)
        if size_bytes > max_bytes:
            db.insert_attachment(
                mail_id,
                AttachmentRecord(
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                    sha256="",
                    local_path="",
                    download_status="skipped_size",
                    error_msg=f"over_limit_{max_mb}MB",
                ),
            )
            skipped += 1
            continue

        sha = hashlib.sha256(payload).hexdigest()
        existing_path = db.find_downloaded_attachment_path_by_sha(sha)
        if existing_path:
            db.insert_attachment(
                mail_id,
                AttachmentRecord(
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                    sha256=sha,
                    local_path=existing_path,
                    download_status="downloaded",
                    error_msg="",
                ),
            )
            downloaded += 1
            continue

        target = _unique_path(mail_dir / filename)
        target.write_bytes(payload)
        db.insert_attachment(
            mail_id,
            AttachmentRecord(
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=sha,
                local_path=str(target),
                download_status="downloaded",
                error_msg="",
            ),
        )
        downloaded += 1

    return AttachmentStats(downloaded=downloaded, skipped=skipped)
