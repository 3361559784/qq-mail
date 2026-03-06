from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import getaddresses

from mail_client import trim_quoted_content


RE_PREFIX_RE = re.compile(r"^(\s*(re|fw|fwd|答复|回复)\s*:\s*)+", re.IGNORECASE)


def normalize_subject(subject: str) -> str:
    normalized = RE_PREFIX_RE.sub("", subject or "")
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized or "(no-subject)"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def parse_address_header(raw: str | None) -> list[str]:
    if not raw:
        return []
    pairs = getaddresses([raw])
    out: list[str] = []
    for _, addr in pairs:
        addr_norm = normalize_email(addr)
        if addr_norm and addr_norm not in out:
            out.append(addr_norm)
    return out


def _parse_iso(iso_text: str) -> datetime:
    value = (iso_text or "").strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_thread_key(
    subject: str,
    sender_email: str,
    to_emails: list[str],
    cc_emails: list[str],
    received_at_utc: str,
) -> str:
    subject_norm = normalize_subject(subject)
    participants = {normalize_email(sender_email)}
    participants.update(normalize_email(x) for x in to_emails)
    participants.update(normalize_email(x) for x in cc_emails)
    participants.discard("")
    ordered = sorted(participants)

    dt = _parse_iso(received_at_utc)
    # 48-hour bucket
    bucket = int(dt.timestamp() // (48 * 3600))
    base = f"{subject_norm}|{','.join(ordered)}|{bucket}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def normalize_body_text(raw_text: str) -> str:
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = trim_quoted_content(text)
    lines = [line.rstrip() for line in text.split("\n")]
    compact = "\n".join(lines)
    compact = re.sub(r"\n{3,}", "\n\n", compact).strip()
    return compact


def stable_content_hash(subject: str, sender_email: str, body_text: str) -> str:
    payload = f"{normalize_subject(subject)}\n{normalize_email(sender_email)}\n{normalize_body_text(body_text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
