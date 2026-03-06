from __future__ import annotations

from dataclasses import dataclass
import logging

from config import Settings
from filter_rules import MailFilter
from mail_client import QQMailClient, compose_reply_body
from model_chain import ModelChainClient
from storage import (
    AllowlistStore,
    DenylistStore,
    FrequentSenderStore,
    ProcessedStore,
    StateStore,
    TableFrequentSenderStore,
    TableProcessedStore,
    resolve_table_connection_string,
)


LOGGER = logging.getLogger("qq-auto-reply")


@dataclass(frozen=True)
class RunStats:
    fetched: int
    replied: int
    skipped: int
    errors: int


def _build_processed_store(settings: Settings, logger: logging.Logger) -> ProcessedStore:
    backend = settings.storage_backend
    conn = resolve_table_connection_string(settings.table_connection_string)

    if backend == "file":
        logger.info("Storage backend=file")
        return StateStore(settings.processed_state_file)

    if backend == "table":
        logger.info("Storage backend=table")
        return TableProcessedStore(
            table_name=settings.processed_table_name,
            connection_string=conn,
        )

    # auto mode
    if conn:
        logger.info("Storage backend=table (auto detected)")
        return TableProcessedStore(
            table_name=settings.processed_table_name,
            connection_string=conn,
        )
    logger.info("Storage backend=file (auto fallback, no table connection)")
    return StateStore(settings.processed_state_file)


def _build_frequent_store(settings: Settings, logger: logging.Logger):
    backend = settings.storage_backend
    conn = resolve_table_connection_string(settings.table_connection_string)

    if backend == "file":
        return FrequentSenderStore(
            path=settings.frequent_sender_file,
            window_days=settings.frequent_window_days,
            min_count=settings.frequent_min_count,
            max_events=settings.frequent_max_events,
        )

    if backend == "table":
        return TableFrequentSenderStore(
            table_name=settings.frequent_table_name,
            window_days=settings.frequent_window_days,
            min_count=settings.frequent_min_count,
            max_events=settings.frequent_max_events,
            connection_string=conn,
        )

    if conn:
        return TableFrequentSenderStore(
            table_name=settings.frequent_table_name,
            window_days=settings.frequent_window_days,
            min_count=settings.frequent_min_count,
            max_events=settings.frequent_max_events,
            connection_string=conn,
        )

    logger.info("Frequent sender store=file (auto fallback)")
    return FrequentSenderStore(
        path=settings.frequent_sender_file,
        window_days=settings.frequent_window_days,
        min_count=settings.frequent_min_count,
        max_events=settings.frequent_max_events,
    )


def _truncate_subject(subject: str, limit: int = 120) -> str:
    clean = " ".join(subject.strip().split())
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit]}..."


def _truncate_preview(text: str, limit: int = 280) -> str:
    one_line = " ".join(text.strip().split())
    if len(one_line) <= limit:
        return one_line
    return f"{one_line[:limit]}..."


def _log_decision(
    logger: logging.Logger,
    action: str,
    sender: str,
    subject: str,
    reason: str,
    dedupe_key: str,
    confidence: float | None = None,
    model: str | None = None,
) -> None:
    parts = [
        "DECISION",
        f"action={action}",
        f"sender={sender}",
        f"subject={_truncate_subject(subject)}",
        f"reason={reason}",
        f"dedupe={dedupe_key}",
    ]
    if confidence is not None:
        parts.append(f"confidence={confidence:.2f}")
    if model:
        parts.append(f"model={model}")
    logger.info(" | ".join(parts))


def run_once(settings: Settings, logger: logging.Logger = LOGGER) -> RunStats:
    model_client = ModelChainClient(
        token=settings.github_token,
        api_url=settings.github_api_url,
        primary=settings.github_model_primary,
        fallbacks=settings.github_model_fallbacks,
        timeout_seconds=settings.model_request_timeout_seconds,
        personalization_dir=settings.personalization_dir,
    )
    mail_client = QQMailClient(
        qq_email=settings.qq_email,
        qq_auth_code=settings.qq_auth_code,
        imap_host=settings.imap_host,
        imap_port=settings.imap_port,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
    )
    state_store = _build_processed_store(settings, logger)
    allowlist = AllowlistStore(settings.allow_senders_file)
    denylist = DenylistStore(settings.deny_senders_file)
    frequent = _build_frequent_store(settings, logger)
    mail_filter = MailFilter(level=settings.filter_level)

    replied = 0
    skipped = 0
    errors = 0

    mails = mail_client.fetch_messages_since(
        fetch_days=settings.imap_fetch_days,
        max_input_chars=settings.max_input_chars,
    )
    logger.info("Fetched %d mail(s) by SINCE window", len(mails))

    for item in mails:
        try:
            if state_store.is_processed(item.dedupe_key):
                skipped += 1
                _log_decision(
                    logger=logger,
                    action="skip",
                    sender=item.sender_email,
                    subject=item.subject,
                    reason="already-processed",
                    dedupe_key=item.dedupe_key,
                )
                continue
        except Exception:
            logger.exception("Failed to check processed state for %s", item.dedupe_key)
            errors += 1
            continue

        sender_lower = item.sender_email.lower().strip()
        if not sender_lower or sender_lower == settings.qq_email.lower():
            try:
                state_store.mark_processed(item.dedupe_key, item.sender_email)
            except Exception:
                logger.exception("Failed to mark self/invalid sender message processed: %s", item.dedupe_key)
                errors += 1
            _log_decision(
                logger=logger,
                action="skip",
                sender=item.sender_email,
                subject=item.subject,
                reason="self-or-invalid-sender",
                dedupe_key=item.dedupe_key,
            )
            skipped += 1
            continue

        allowlist_hit = allowlist.contains(item.sender_email)
        denylist_hit = denylist.contains(item.sender_email)
        frequent_hit = False
        try:
            frequent_hit = frequent.is_frequent(item.sender_email)
        except Exception:
            logger.exception("Failed to evaluate frequent sender for %s", item.sender_email)
            errors += 1

        decision = mail_filter.evaluate(
            headers=item.headers,
            sender=item.sender_email,
            subject=item.subject,
            body=item.body,
            denylist_hit=denylist_hit,
            allowlist_hit=allowlist_hit,
            frequent_hit=frequent_hit,
        )
        if not decision.should_reply:
            logger.info(
                "Skip sender=%s subject=%s reason=%s confidence=%.2f",
                item.sender_email,
                item.subject,
                decision.reason,
                decision.confidence,
            )
            _log_decision(
                logger=logger,
                action="skip",
                sender=item.sender_email,
                subject=item.subject,
                reason=decision.reason,
                dedupe_key=item.dedupe_key,
                confidence=decision.confidence,
            )
            try:
                state_store.mark_processed(item.dedupe_key, item.sender_email)
            except Exception:
                logger.exception("Failed to mark skipped message processed: %s", item.dedupe_key)
                errors += 1
            skipped += 1
            continue

        try:
            model_reply = model_client.generate_reply(
                sender=item.sender_display,
                subject=item.subject,
                body=item.body,
            )
        except Exception:
            logger.exception(
                "Model generation failed dedupe=%s sender=%s",
                item.dedupe_key,
                item.sender_email,
            )
            errors += 1
            continue

        try:
            # Concurrent-safe dedupe claim: only one instance can create this key.
            claimed = state_store.mark_processed(item.dedupe_key, item.sender_email)
        except Exception:
            logger.exception("Failed to claim message before sending: %s", item.dedupe_key)
            errors += 1
            continue
        if not claimed:
            skipped += 1
            logger.info("Skip dedupe claimed by another worker: %s", item.dedupe_key)
            _log_decision(
                logger=logger,
                action="skip",
                sender=item.sender_email,
                subject=item.subject,
                reason="claimed-by-other-worker",
                dedupe_key=item.dedupe_key,
            )
            continue

        body = compose_reply_body(
            ai_text=model_reply.text,
            reply_signature=settings.reply_signature,
            model_signature_template=settings.model_signature_template,
            used_model=model_reply.used_model,
            enable_postprocess=settings.enable_reply_postprocess,
            max_questions=settings.reply_max_questions,
        )
        try:
            mail = mail_client.build_reply_email(
                original=item.original,
                to_addr=item.sender_email,
                final_body=body,
            )
            mail_client.send_email(mail)
            logger.info(
                "REPLY_SENT | to=%s | model=%s | dedupe=%s | body_preview=%s",
                item.sender_email,
                model_reply.used_model,
                item.dedupe_key,
                _truncate_preview(body),
            )

            if settings.self_notify_on_reply:
                notify_to = (settings.self_notify_email or settings.qq_email).strip()
                if notify_to:
                    try:
                        notify_mail = mail_client.build_delivery_receipt_email(
                            notify_to=notify_to,
                            replied_to=item.sender_email,
                            original_subject=item.subject,
                            final_body=body,
                            used_model=model_reply.used_model,
                            dedupe_key=item.dedupe_key,
                            body_chars=settings.self_notify_body_chars,
                        )
                        mail_client.send_email(notify_mail)
                        logger.info(
                            "NOTIFY_SENT | notify_to=%s | replied_to=%s | dedupe=%s",
                            notify_to,
                            item.sender_email,
                            item.dedupe_key,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to send self notify mail replied_to=%s dedupe=%s",
                            item.sender_email,
                            item.dedupe_key,
                            exc_info=True,
                        )

            try:
                mail_client.mark_answered(item.uid)
            except Exception:
                logger.warning("Failed to mark answered for uid=%s", item.uid, exc_info=True)
            try:
                frequent.record(item.sender_email)
            except Exception:
                logger.warning("Failed to persist frequent sender: %s", item.sender_email, exc_info=True)
            logger.info(
                "Replied sender=%s model=%s attempts=%s",
                item.sender_email,
                model_reply.used_model,
                ",".join(model_reply.attempted_models),
            )
            _log_decision(
                logger=logger,
                action="reply",
                sender=item.sender_email,
                subject=item.subject,
                reason="sent",
                dedupe_key=item.dedupe_key,
                model=model_reply.used_model,
            )
            replied += 1
        except Exception:
            logger.exception("SMTP send failed dedupe=%s sender=%s", item.dedupe_key, item.sender_email)
            _log_decision(
                logger=logger,
                action="error",
                sender=item.sender_email,
                subject=item.subject,
                reason="smtp-send-failed",
                dedupe_key=item.dedupe_key,
            )
            try:
                state_store.clear_processed(item.dedupe_key)
            except Exception:
                logger.warning(
                    "Failed to clear processed state after send error: %s",
                    item.dedupe_key,
                    exc_info=True,
                )
            errors += 1
            continue

    return RunStats(fetched=len(mails), replied=replied, skipped=skipped, errors=errors)
