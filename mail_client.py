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


def truncate_text(text: str, limit: int) -> str:
    if limit <= 0:
        return text
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


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


def _normalize_line_for_dedupe(line: str) -> str:
    lowered = line.strip().lower()
    return re.sub(r"[，。！？!?,.\s]+", "", lowered)


def _is_closing_line(line: str) -> bool:
    stripped = line.strip()
    lowered = stripped.lower()
    closing_patterns = (
        r"^祝好[！!，,。.]?$",
        r"^此致[！!，,。.]?$",
        r"^敬礼[！!，,。.]?$",
        r"^顺颂商祺[！!，,。.]?$",
        r"^best regards[!,. ]*$",
        r"^kind regards[!,. ]*$",
        r"^regards[!,. ]*$",
        r"^sincerely[!,. ]*$",
    )
    return any(re.match(pattern, lowered, re.IGNORECASE) for pattern in closing_patterns)


def _is_signature_line(line: str) -> bool:
    stripped = line.strip()
    lowered = stripped.lower()
    if not stripped:
        return True
    if re.search(r"\[(recipient's name|your name|your position|your company|您的姓名|您的职位|您的公司)\]", stripped, re.I):
        return True
    if re.match(r"^(name|position|company)\s*:", lowered):
        return True
    if re.search(r"(研究工程师|工程师|职位|公司|university|linkedin)", stripped, re.I) and len(stripped) <= 40:
        return True
    if re.search(r"[@#].+\.(com|cn|net|org)$", lowered):
        return True
    if re.search(r"\+?\d[\d\- ]{6,}", stripped):
        return True
    return False


def _strip_trailing_signature_block(lines: list[str]) -> list[str]:
    kept = list(lines)
    while kept and not kept[-1].strip():
        kept.pop()

    preserved_closing: str | None = None
    while kept:
        tail = kept[-1].strip()
        if not tail:
            kept.pop()
            continue
        if _is_signature_line(tail):
            kept.pop()
            continue
        if _is_closing_line(tail):
            if preserved_closing is None:
                preserved_closing = kept.pop().strip()
                continue
            kept.pop()
            continue
        break

    while kept and not kept[-1].strip():
        kept.pop()
    if preserved_closing:
        kept.append(preserved_closing)
    return kept


def _is_template_header_line(line: str) -> bool:
    stripped = line.strip()
    lowered = stripped.lower()
    if not stripped:
        return False
    if re.match(r"^(subject|from|to)\s*:", lowered):
        return True
    if re.match(r"^dear\s+sir\s*/\s*madam[,:]?$", lowered):
        return True
    if re.search(r"\[(recipient's name|your name|your position|your company|您的姓名|您的职位|您的公司)\]", stripped, re.I):
        return True
    return False


def _limit_question_marks(text: str, max_questions: int) -> str:
    if max_questions < 0:
        max_questions = 0
    out_chars: list[str] = []
    questions = 0
    for ch in text:
        if ch in {"?", "？"}:
            questions += 1
            if questions > max_questions:
                out_chars.append("。")
                continue
        out_chars.append(ch)
    return "".join(out_chars)


def sanitize_reply_text(text: str, max_questions: int = 1) -> str:
    raw_lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    filtered_lines = [line for line in raw_lines if not _is_template_header_line(line)]

    # Drop consecutive duplicates such as repeated "祝好" lines.
    deduped_lines: list[str] = []
    prev_norm = ""
    for line in filtered_lines:
        normalized = _normalize_line_for_dedupe(line)
        if normalized and normalized == prev_norm:
            continue
        deduped_lines.append(line)
        prev_norm = normalized or prev_norm

    cleaned_lines = _strip_trailing_signature_block(deduped_lines)
    body = "\n".join(cleaned_lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = _limit_question_marks(body, max_questions=max_questions)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def compose_reply_body(
    ai_text: str,
    reply_signature: str,
    model_signature_template: str,
    used_model: str,
    enable_postprocess: bool = True,
    max_questions: int = 1,
) -> str:
    base_text = ai_text.strip()
    if enable_postprocess:
        base_text = sanitize_reply_text(base_text, max_questions=max_questions)
    if not base_text:
        base_text = "已收到您的来信，我会尽快处理并回复关键结果。"
    chunks = [base_text]
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

            typ, data = imap.search(None, "SINCE", since_date, "UNANSWERED")
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

    def build_delivery_receipt_email(
        self,
        notify_to: str,
        replied_to: str,
        original_subject: str,
        final_body: str,
        used_model: str,
        dedupe_key: str,
        body_chars: int = 1200,
    ) -> EmailMessage:
        out = EmailMessage()
        out["From"] = self.qq_email
        out["To"] = notify_to
        out["Subject"] = (
            f"[qq-mail] 已自动回复 -> {replied_to} | "
            f"{truncate_text((original_subject or '(无主题)').strip(), 60)}"
        )
        out["Date"] = formatdate(localtime=True)
        out["Message-ID"] = make_msgid()

        preview = truncate_text(final_body.strip(), body_chars).strip()
        out.set_content(
            "\n".join(
                [
                    "自动回复已发送。",
                    f"收件人: {replied_to}",
                    f"原主题: {(original_subject or '(无主题)').strip()}",
                    f"模型: {used_model}",
                    f"判重键: {dedupe_key}",
                    "",
                    "发送正文:",
                    "-----",
                    preview,
                ]
            ).strip()
        )
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
