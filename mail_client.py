from __future__ import annotations

import html
import imaplib
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parseaddr


@dataclass(frozen=True)
class IncomingMail:
    uid: str
    dedupe_key: str
    sender_name: str
    sender_email: str
    sender_display: str
    subject: str
    body: str
    headers: dict[str, str]
    original: EmailMessage


def decode_mime(value: str | None, fallback: str = "") -> str:
    if not value:
        return fallback
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def normalize_subject(subject: str) -> str:
    clean = subject.strip() or "(无主题)"
    if clean.lower().startswith("re:"):
        return clean
    return f"Re: {clean}"


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", "", raw_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def trim_quoted_content(text: str) -> str:
    lines = text.splitlines()
    stop_markers = (
        "on ",
        "发件人:",
        "from:",
        "-----original message-----",
        "----original message----",
    )
    kept: list[str] = []
    for line in lines:
        lowered = line.strip().lower()
        if lowered.startswith(">"):
            continue
        if any(lowered.startswith(marker) for marker in stop_markers):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def extract_body(msg: EmailMessage) -> str:
    plain_texts: list[str] = []
    html_texts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    plain_texts.append(part.get_content())
                except Exception:
                    pass
            elif ctype == "text/html":
                try:
                    html_texts.append(part.get_content())
                except Exception:
                    pass
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            plain_texts.append(msg.get_content())
        elif ctype == "text/html":
            html_texts.append(msg.get_content())

    if plain_texts:
        return trim_quoted_content("\n\n".join(t.strip() for t in plain_texts if t.strip()))
    if html_texts:
        return trim_quoted_content(html_to_text("\n\n".join(html_texts)))
    return ""


def compose_reply_body(
    ai_text: str,
    reply_signature: str,
    model_signature_template: str,
    used_model: str,
) -> str:
    chunks = [ai_text.strip()]
    signature = reply_signature.strip()
    if signature:
        chunks.append(signature)
    model_signature = model_signature_template.format(model=used_model).strip()
    if model_signature:
        chunks.append(model_signature)
    return "\n\n".join(chunks).strip()


class QQMailClient:
    def __init__(
        self,
        qq_email: str,
        qq_auth_code: str,
        imap_host: str,
        imap_port: int,
        smtp_host: str,
        smtp_port: int,
    ) -> None:
        self.qq_email = qq_email
        self.qq_auth_code = qq_auth_code
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port

    def _since_date(self, fetch_days: int) -> str:
        date_obj = datetime.now() - timedelta(days=max(fetch_days, 1))
        return date_obj.strftime("%d-%b-%Y")

    @staticmethod
    def _extract_uid(meta: bytes) -> str:
        text = meta.decode(errors="ignore")
        match = re.search(r"UID (\d+)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _mail_headers(msg: EmailMessage) -> dict[str, str]:
        return {key: str(value) for key, value in msg.items()}

    def fetch_messages_since(self, fetch_days: int, max_input_chars: int) -> list[IncomingMail]:
        out: list[IncomingMail] = []
        since_date = self._since_date(fetch_days)

        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as imap:
            imap.login(self.qq_email, self.qq_auth_code)
            imap.select("INBOX")

            typ, data = imap.search(None, "SINCE", since_date)
            if typ != "OK":
                raise RuntimeError(f"IMAP search failed: {typ} {data}")
            msg_nums = data[0].split() if data and data[0] else []

            for msg_num in msg_nums:
                typ, fetched = imap.fetch(msg_num, "(BODY.PEEK[] UID FLAGS)")
                if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue

                meta = fetched[0][0]
                raw_msg = fetched[0][1]
                uid = self._extract_uid(meta)

                msg = BytesParser(policy=policy.default).parsebytes(raw_msg)
                sender_name_raw, sender_email = parseaddr(msg.get("From", ""))
                sender_name = decode_mime(sender_name_raw, sender_email)
                subject = decode_mime(msg.get("Subject"), "(无主题)")
                body = extract_body(msg).strip() or "（邮件正文为空或仅包含附件）"
                if len(body) > max_input_chars:
                    body = body[:max_input_chars]

                message_id = (msg.get("Message-ID") or "").strip()
                dedupe_key = message_id or (f"imap-uid:{uid}" if uid else f"imap-num:{msg_num.decode()}")
                out.append(
                    IncomingMail(
                        uid=uid,
                        dedupe_key=dedupe_key,
                        sender_name=sender_name,
                        sender_email=sender_email,
                        sender_display=sender_name or sender_email,
                        subject=subject,
                        body=body,
                        headers=self._mail_headers(msg),
                        original=msg,
                    )
                )
        return out

    def build_reply_email(
        self,
        original: EmailMessage,
        to_addr: str,
        final_body: str,
    ) -> EmailMessage:
        out = EmailMessage()
        out["From"] = self.qq_email
        out["To"] = to_addr
        out["Subject"] = normalize_subject(decode_mime(original.get("Subject"), "(无主题)"))
        out["Date"] = formatdate(localtime=True)
        out["Message-ID"] = make_msgid()

        original_msg_id = (original.get("Message-ID") or "").strip()
        original_refs = (original.get("References") or "").strip()
        if original_msg_id:
            out["In-Reply-To"] = original_msg_id
            out["References"] = f"{original_refs} {original_msg_id}".strip()

        out.set_content(final_body.strip())
        return out

    def send_email(self, mail: EmailMessage) -> None:
        with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=60) as smtp:
            smtp.login(self.qq_email, self.qq_auth_code)
            smtp.send_message(mail)

    def mark_answered(self, uid: str) -> None:
        if not uid:
            return
        with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as imap:
            imap.login(self.qq_email, self.qq_auth_code)
            imap.select("INBOX")
            imap.uid("STORE", uid, "+FLAGS", r"(\Seen \Answered)")
