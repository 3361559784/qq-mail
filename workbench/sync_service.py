from __future__ import annotations

import logging
from dataclasses import dataclass

from config import Settings
from workbench.db import WorkbenchDB
from workbench.embed_store import EmbeddingClient, rebuild_faiss_from_sqlite, upsert_embedding_for_mail
from workbench.ingest import IngestService
from workbench.llm_triage import LlmTriageClient
from workbench.lock import SyncLock
from workbench.models import SyncStats, utc_now_iso
from workbench.plugins.github_notifications import create_github_tasks, extract_github_entities, match_github_notification
from workbench.rules import triage_by_rules
from workbench.tasks import make_task_drafts, merge_decision


LOGGER = logging.getLogger("qq-workbench")


@dataclass(frozen=True)
class WorkbenchRunResult:
    stats: SyncStats
    lock_acquired: bool


def should_call_llm(rule_is_candidate: bool, only_candidates: bool) -> bool:
    if only_candidates:
        return rule_is_candidate
    return True


class SyncService:
    def __init__(self, settings: Settings, logger: logging.Logger = LOGGER) -> None:
        self.settings = settings
        self.logger = logger
        self.db = WorkbenchDB(settings.workbench_db_path)
        self.db.init_schema()

        self.ingest_service = IngestService(
            db=self.db,
            qq_email=settings.qq_email,
            qq_auth_code=settings.qq_auth_code,
            imap_host=settings.imap_host,
            imap_port=settings.imap_port,
            attach_dir=settings.workbench_attach_dir,
            attach_max_mb=settings.workbench_attach_max_mb,
        )
        self.llm_client = LlmTriageClient(
            token=settings.github_token,
            api_url=settings.github_api_url,
            model=settings.workbench_llm_model,
            timeout_seconds=settings.model_request_timeout_seconds,
        )
        self.embedding_client = EmbeddingClient(
            token=settings.github_token,
            api_url=settings.github_embedding_api_url,
            model=settings.workbench_embed_model,
            timeout_seconds=settings.model_request_timeout_seconds,
        )

    def run_once(self) -> WorkbenchRunResult:
        sync_lock = SyncLock(self.db, ttl_seconds=max(self.settings.workbench_sync_interval_seconds * 2, 60))
        if not sync_lock.try_acquire():
            self.logger.info("WORKBENCH_SYNC | skipped | reason=lock_not_acquired")
            stats = SyncStats(
                fetched=0,
                inserted_or_updated=0,
                llm_called=0,
                tasks_created=0,
                attachments_downloaded=0,
                attachments_skipped=0,
                errors=0,
                finished_at_utc=utc_now_iso(),
            )
            return WorkbenchRunResult(stats=stats, lock_acquired=False)

        llm_called = 0
        tasks_created = 0
        errors = 0
        embedding_changed = False

        try:
            ingest = self.ingest_service.fetch_incremental(initial_days=self.settings.workbench_sync_days_initial)
            mail_rows = self.db.list_mails(limit=max(ingest.upserted, 1))

            for row in mail_rows:
                try:
                    rule = triage_by_rules(
                        sender_email=str(row["sender_email"] or ""),
                        subject=str(row["subject"] or ""),
                        body_text=str(row["body_text"] or ""),
                        headers_json=str(row["headers_json"] or "{}"),
                        flags_json=str(row["flags_json"] or "[]"),
                    )

                    llm_result = None
                    llm_parse_failed = False
                    invoke_llm = should_call_llm(
                        rule_is_candidate=rule.is_candidate,
                        only_candidates=self.settings.workbench_llm_only_candidates,
                    )

                    if invoke_llm:
                        llm_called += 1
                        r = self.llm_client.triage(
                            sender_email=str(row["sender_email"] or ""),
                            subject=str(row["subject"] or ""),
                            body_text=str(row["body_text"] or ""),
                        )
                        llm_result = r.decision
                        llm_parse_failed = r.parse_failed

                    final = merge_decision(rule=rule, llm=llm_result, llm_parse_failed=llm_parse_failed)
                    mail_id = int(row["id"])
                    self.db.upsert_triage(mail_id=mail_id, decision=final, triaged_at_utc=utc_now_iso())

                    tasks = make_task_drafts(subject=str(row["subject"] or ""), final_decision=final)

                    if match_github_notification(
                        sender_email=str(row["sender_email"] or ""),
                        subject=str(row["subject"] or ""),
                        headers_json=str(row["headers_json"] or "{}"),
                    ):
                        entity = extract_github_entities(
                            subject=str(row["subject"] or ""),
                            body_text=str(row["body_text"] or ""),
                        )
                        if entity is not None:
                            tasks.extend(create_github_tasks(entity))

                    tasks_created += self.db.insert_tasks(
                        mail_id=mail_id,
                        tasks=tasks,
                        created_at_utc=utc_now_iso(),
                    )

                    if final.category in {"action", "waiting"} or bool(tasks):
                        if upsert_embedding_for_mail(
                            db=self.db,
                            client=self.embedding_client,
                            mail_row=row,
                        ):
                            embedding_changed = True

                    self.logger.info(
                        "WORKBENCH_TRIAGE | mail_id=%s | category=%s | strategy=%s | llm_called=%s | tasks=%s",
                        mail_id,
                        final.category,
                        final.strategy,
                        invoke_llm,
                        len(tasks),
                    )
                except Exception:
                    self.logger.exception("WORKBENCH_MAIL_PROCESS_FAILED | mail_id=%s", row["id"])
                    errors += 1

            if embedding_changed:
                vector_count = rebuild_faiss_from_sqlite(
                    db=self.db,
                    model=self.settings.workbench_embed_model,
                    index_path=self.settings.workbench_faiss_index_path,
                )
                self.logger.info("WORKBENCH_FAISS_REBUILT | vectors=%d", vector_count)

            stats = SyncStats(
                fetched=ingest.fetched,
                inserted_or_updated=ingest.upserted,
                llm_called=llm_called,
                tasks_created=tasks_created,
                attachments_downloaded=ingest.attachments_downloaded,
                attachments_skipped=ingest.attachments_skipped,
                errors=errors,
                finished_at_utc=utc_now_iso(),
            )
            self.logger.info(
                "WORKBENCH_SYNC_DONE | fetched=%d | upserted=%d | llm_called=%d | tasks=%d | attachments_downloaded=%d | attachments_skipped=%d | errors=%d",
                stats.fetched,
                stats.inserted_or_updated,
                stats.llm_called,
                stats.tasks_created,
                stats.attachments_downloaded,
                stats.attachments_skipped,
                stats.errors,
            )
            return WorkbenchRunResult(stats=stats, lock_acquired=True)
        finally:
            sync_lock.release()
