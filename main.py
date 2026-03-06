#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import time

import uvicorn

from config import load_settings
from runner import run_once
from workbench.db import WorkbenchDB
from workbench.embed_store import EmbeddingClient
from workbench.search import SearchService
from workbench.sync_service import SyncService
from workbench.web_app import create_workbench_app


LOGGER = logging.getLogger("qq-auto-reply")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QQ Mail automation")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    parser.add_argument("--once", action="store_true", help="Run one cycle (legacy auto-reply compatibility)")

    subparsers = parser.add_subparsers(dest="command")

    p_auto = subparsers.add_parser("auto-reply", help="Run legacy auto-reply loop")
    p_auto.add_argument("--once", action="store_true", help="Run one poll cycle and exit")

    p_web = subparsers.add_parser("workbench-web", help="Run local workbench web server")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8787)
    p_web.add_argument("--no-scheduler", action="store_true", help="Disable internal sync scheduler")

    subparsers.add_parser("workbench-sync", help="Run one workbench sync cycle")

    p_search = subparsers.add_parser("workbench-search", help="Run one vector query")
    p_search.add_argument("query")

    p_tasks = subparsers.add_parser("workbench-tasks", help="List tasks")
    p_tasks.add_argument("--status", default="open")

    return parser.parse_args()


def _run_auto_reply(settings, once: bool) -> None:  # type: ignore[no-untyped-def]
    LOGGER.info(
        "QQ auto-reply started primary=%s fallbacks=%s poll=%ss backend=%s",
        settings.github_model_primary,
        settings.github_model_fallbacks,
        settings.poll_seconds,
        settings.storage_backend,
    )

    while True:
        try:
            stats = run_once(settings=settings, logger=LOGGER)
            LOGGER.info(
                "Cycle done fetched=%d replied=%d skipped=%d errors=%d",
                stats.fetched,
                stats.replied,
                stats.skipped,
                stats.errors,
            )
        except KeyboardInterrupt:
            LOGGER.info("Interrupted, exiting.")
            break
        except Exception:
            LOGGER.exception("Poll cycle failed")

        if once:
            break
        time.sleep(max(settings.poll_seconds, 5))


def _run_workbench_sync(settings) -> None:  # type: ignore[no-untyped-def]
    service = SyncService(settings=settings)
    result = service.run_once()
    LOGGER.info(
        "WORKBENCH_SYNC_RESULT lock=%s fetched=%d upserted=%d llm_called=%d tasks=%d attachments_downloaded=%d attachments_skipped=%d errors=%d",
        result.lock_acquired,
        result.stats.fetched,
        result.stats.inserted_or_updated,
        result.stats.llm_called,
        result.stats.tasks_created,
        result.stats.attachments_downloaded,
        result.stats.attachments_skipped,
        result.stats.errors,
    )


def _run_workbench_search(settings, query: str) -> None:  # type: ignore[no-untyped-def]
    db = WorkbenchDB(settings.workbench_db_path)
    db.init_schema()
    service = SearchService(
        db=db,
        embedding_client=EmbeddingClient(
            token=settings.github_token,
            api_url=settings.github_embedding_api_url,
            model=settings.workbench_embed_model,
        ),
        index_path=settings.workbench_faiss_index_path,
        llm_token=settings.github_token,
        llm_api_url=settings.github_api_url,
        llm_model=settings.workbench_llm_model,
    )
    answer = service.answer_with_evidence(query=query, top_k=settings.workbench_vector_top_k)
    print("Answer:")
    print(answer.answer)
    print("\nHits:")
    for hit in answer.hits:
        print(f"- mail_id={hit.mail_id} score={hit.score:.3f} sender={hit.sender} subject={hit.subject}")


def _run_workbench_tasks(settings, status: str) -> None:  # type: ignore[no-untyped-def]
    db = WorkbenchDB(settings.workbench_db_path)
    db.init_schema()
    rows = db.list_tasks(status=status)
    for row in rows:
        print(f"#{row['id']} [{row['status']}] ({row['priority']}) {row['title']} | mail={row['mail_id']}")


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

    cmd = args.command or "auto-reply"
    if cmd == "auto-reply":
        _run_auto_reply(settings=settings, once=bool(getattr(args, "once", False)))
        return
    if cmd == "workbench-sync":
        _run_workbench_sync(settings=settings)
        return
    if cmd == "workbench-search":
        _run_workbench_search(settings=settings, query=args.query)
        return
    if cmd == "workbench-tasks":
        _run_workbench_tasks(settings=settings, status=args.status)
        return
    if cmd == "workbench-web":
        app = create_workbench_app(settings=settings, enable_scheduler=not args.no_scheduler)
        uvicorn.run(app, host=args.host, port=args.port)
        return

    raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
