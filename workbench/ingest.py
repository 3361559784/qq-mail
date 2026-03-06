from __future__ import annotations

import email.utils
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from mail_client import decode_mime, extract_body, html_to_text
from workbench.attachments import AttachmentStats, download_attachments
from workbench.db import WorkbenchDB
from workbench.models import MailRecord, utc_now_iso
from workbench.normalize import build_thread_key, normalize_body_text, parse_address_header


@dataclass(frozen=True)
class IngestResult:
    fetched: int
    upserted: int
    attachments_downloaded: int
    attachments_skipped: int


class IngestService:
    def __init__(
        self,
        db: WorkbenchDB,
        qq_email: str,
        qq_auth_code: str,
        imap_host: str,
        imap_port: int,
        attach_dir,
        attach_max_mb: int,
    ) -> None:
        self.db = db
        self.qq_email = qq_email
        self.qq_auth_code = qq_auth_code
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.attach_dir = attach_dir
        self.attach_max_mb = attach_max_mb

    @staticmethod
    def _extract_uid(meta: bytes) -> str:
        text = meta.decode(errors="ignore")
        match = re.search(r"UID (\d+)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_flags(meta: bytes) -> list[str]:
        text = meta.decode(errors="ignore")
        match = re.search(r"FLAGS \((.*?)\)", text)
        if not match:
            return []
        raw_flags = match.group(1).strip()
        if not raw_flags:
            return []
        return [flag.strip() for flag in raw_flags.split() if flag.strip()]

    @staticmethod
    def _extract_body_html(msg: EmailMessage) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html" and part.get_content_disposition() != "attachment":
                    try:
                        return str(part.get_content())
                    except Exception:
                        continue
            return ""
        if msg.get_content_type() == "text/html":
            try:
                return str(msg.get_content())
            except Exception:
                return ""
        return ""

    @staticmethod
    def _parse_received_at(msg: EmailMessage) -> str:
        raw_date = msg.get("Date", "")
        try:
            dt = email.utils.parsedate_to_datetime(raw_date)
            if dt is None:
                raise ValueError("invalid date")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except Exception:
            return utc_now_iso()

    def _resolve_since_date(self, initial_days: int) -> str:
        last_sync = self.db.get_state("last_sync_ts", "").strip()
        if last_sync:
            try:
                iso = last_sync[:-1] + "+00:00" if last_sync.endswith("Z") else last_sync
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).strftime("%d-%b-%Y")
            except Exception:
                pass
        dt = datetime.now(timezone.utc) - timedelta(days=max(initial_days, 1))
        return dt.strftime("%d-%b-%Y")

    def fetch_incremental(self, initial_days: int = 7) -> IngestResult:
        since_date = self._resolve_since_date(initial_days=initial_days)
        fetched = 0
        upserted = 0
        attachment_downloaded = 0
        attachment_skipped = 0

        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as imap:
            imap.login(self.qq_email, self.qq_auth_code)
            imap.select("INBOX")
            typ, data = imap.search(None, "SINCE", since_date)
            if typ != "OK":
                raise RuntimeError(f"IMAP search failed: {typ} {data}")
            msg_nums = data[0].split() if data and data[0] else []

            for msg_num in msg_nums:
                typ, fetched_data = imap.fetch(msg_num, "(BODY.PEEK[] UID FLAGS)")
                if typ != "OK" or not fetched_data or not isinstance(fetched_data[0], tuple):
                    continue

                fetched += 1
                meta = fetched_data[0][0]
                raw_msg = fetched_data[0][1]

                uid = self._extract_uid(meta)
                flags = self._extract_flags(meta)

                msg = BytesParser(policy=policy.default).parsebytes(raw_msg)
                sender_name_raw, sender_email = email.utils.parseaddr(msg.get("From", ""))
                sender_name = decode_mime(sender_name_raw, sender_email)
                sender_email = sender_email.strip().lower()
                subject = decode_mime(msg.get("Subject"), "(无主题)")

                to_emails = parse_address_header(msg.get("To", ""))
                cc_emails = parse_address_header(msg.get("Cc", ""))

                raw_body_text = extract_body(msg)
                body_text = normalize_body_text(raw_body_text) or "（邮件正文为空或仅包含附件）"
                body_html = self._extract_body_html(msg)
                if body_html:
                    body_html = body_html.strip()
                    if not body_text:
                        body_text = normalize_body_text(html_to_text(body_html))

                received_at_utc = self._parse_received_at(msg)
                message_id = (msg.get("Message-ID") or "").strip() or (
                    f"imap-uid:{uid}" if uid else f"imap-num:{msg_num.decode()}"
                )
                headers_json = (
                    __import__("json").dumps({k: str(v) for k, v in msg.items()}, ensure_ascii=False, sort_keys=True)
                )
                flags_json = __import__("json").dumps(flags, ensure_ascii=False)

                thread_key = build_thread_key(
                    subject=subject,
                    sender_email=sender_email,
                    to_emails=to_emails,
                    cc_emails=cc_emails,
                    received_at_utc=received_at_utc,
                )

                record = MailRecord(
                    message_id=message_id,
                    thread_key=thread_key,
                    sender_email=sender_email,
                    sender_name=sender_name,
                    to_emails=to_emails,
                    cc_emails=cc_emails,
                    subject=subject,
                    received_at_utc=received_at_utc,
                    body_text=body_text,
                    body_html=body_html,
                    headers_json=headers_json,
                    flags_json=flags_json,
                    ingested_at_utc=utc_now_iso(),
                )

                mail_id = self.db.upsert_mail(record)
                upserted += 1

                att_stats: AttachmentStats = download_attachments(
                    db=self.db,
                    msg=msg,
                    mail_id=mail_id,
                    attach_root=self.attach_dir,
                    max_mb=self.attach_max_mb,
                )
                attachment_downloaded += att_stats.downloaded
                attachment_skipped += att_stats.skipped

        self.db.set_state("last_sync_ts", utc_now_iso())
        return IngestResult(
            fetched=fetched,
            upserted=upserted,
            attachments_downloaded=attachment_downloaded,
            attachments_skipped=attachment_skipped,
        )
