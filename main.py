#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import time

from config import load_settings
from filter_rules import MailFilter
from mail_client import QQMailClient, compose_reply_body
from model_chain import ModelChainClient
from storage import AllowlistStore, FrequentSenderStore, StateStore


LOGGER = logging.getLogger("qq-auto-reply")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QQ Mail auto-reply bot powered by GitHub Models")
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    return parser.parse_args()


def run_cycle(
    client: QQMailClient,
    model_client: ModelChainClient,
    state_store: StateStore,
    allowlist: AllowlistStore,
    frequent: FrequentSenderStore,
    mail_filter: MailFilter,
    settings,
) -> tuple[int, int]:
    replied = 0
    skipped = 0
    mails = client.fetch_messages_since(
        fetch_days=settings.imap_fetch_days,
        max_input_chars=settings.max_input_chars,
    )
    LOGGER.info("Fetched %d mail(s) by SINCE window", len(mails))

    for item in mails:
        if state_store.is_processed(item.dedupe_key):
            skipped += 1
            continue

        sender_lower = item.sender_email.lower().strip()
        if not sender_lower or sender_lower == settings.qq_email.lower():
            state_store.mark_processed(item.dedupe_key)
            skipped += 1
            continue

        allowlist_hit = allowlist.contains(item.sender_email)
        frequent_hit = frequent.is_frequent(item.sender_email)
        decision = mail_filter.evaluate(
            headers=item.headers,
            sender=item.sender_email,
            subject=item.subject,
            body=item.body,
            allowlist_hit=allowlist_hit,
            frequent_hit=frequent_hit,
        )
        if not decision.should_reply:
            LOGGER.info(
                "Skip sender=%s subject=%s reason=%s confidence=%.2f",
                item.sender_email,
                item.subject,
                decision.reason,
                decision.confidence,
            )
            state_store.mark_processed(item.dedupe_key)
            skipped += 1
            continue

        try:
            model_reply = model_client.generate_reply(
                sender=item.sender_display,
                subject=item.subject,
                body=item.body,
            )
        except Exception:
            LOGGER.exception(
                "Model generation failed dedupe=%s sender=%s",
                item.dedupe_key,
                item.sender_email,
            )
            continue

        body = compose_reply_body(
            ai_text=model_reply.text,
            reply_signature=settings.reply_signature,
            model_signature_template=settings.model_signature_template,
            used_model=model_reply.used_model,
        )
        try:
            mail = client.build_reply_email(
                original=item.original,
                to_addr=item.sender_email,
                final_body=body,
            )
            client.send_email(mail)
            client.mark_answered(item.uid)
        except Exception:
            LOGGER.exception("SMTP send failed dedupe=%s sender=%s", item.dedupe_key, item.sender_email)
            continue

        state_store.mark_processed(item.dedupe_key)
        frequent.record(item.sender_email)
        LOGGER.info(
            "Replied sender=%s model=%s attempts=%s",
            item.sender_email,
            model_reply.used_model,
            ",".join(model_reply.attempted_models),
        )
        replied += 1

    return replied, skipped


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        settings = load_settings()
    except Exception as exc:
        raise SystemExit(f"Config error: {exc}") from exc

    model_client = ModelChainClient(
        token=settings.github_token,
        api_url=settings.github_api_url,
        primary=settings.github_model_primary,
        fallbacks=settings.github_model_fallbacks,
        timeout_seconds=settings.model_request_timeout_seconds,
    )
    mail_client = QQMailClient(
        qq_email=settings.qq_email,
        qq_auth_code=settings.qq_auth_code,
        imap_host=settings.imap_host,
        imap_port=settings.imap_port,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
    )
    state_store = StateStore(settings.processed_state_file)
    allowlist = AllowlistStore(settings.allow_senders_file)
    frequent = FrequentSenderStore(
        path=settings.frequent_sender_file,
        window_days=settings.frequent_window_days,
        min_count=settings.frequent_min_count,
        max_events=settings.frequent_max_events,
    )
    mail_filter = MailFilter(level=settings.filter_level)

    LOGGER.info(
        "QQ auto-reply started primary=%s fallbacks=%s poll=%ss",
        settings.github_model_primary,
        settings.github_model_fallbacks,
        settings.poll_seconds,
    )

    while True:
        try:
            replied, skipped = run_cycle(
                client=mail_client,
                model_client=model_client,
                state_store=state_store,
                allowlist=allowlist,
                frequent=frequent,
                mail_filter=mail_filter,
                settings=settings,
            )
            LOGGER.info("Cycle done replied=%d skipped=%d", replied, skipped)
        except KeyboardInterrupt:
            LOGGER.info("Interrupted, exiting.")
            break
        except Exception:
            LOGGER.exception("Poll cycle failed")

        if args.once:
            break
        time.sleep(max(settings.poll_seconds, 5))


if __name__ == "__main__":
    main()
