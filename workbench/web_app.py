from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import Settings
from workbench.db import WorkbenchDB
from workbench.embed_store import EmbeddingClient
from workbench.search import SearchService
from workbench.sync_service import SyncService


LOGGER = logging.getLogger("qq-workbench")


class _SchedulerThread(threading.Thread):
    def __init__(self, settings: Settings, stop_event: threading.Event) -> None:
        super().__init__(daemon=True)
        self.settings = settings
        self.stop_event = stop_event

    def run(self) -> None:
        service = SyncService(settings=self.settings, logger=LOGGER)
        interval = max(self.settings.workbench_sync_interval_seconds, 60)
        while not self.stop_event.is_set():
            try:
                service.run_once()
            except Exception:
                LOGGER.exception("WORKBENCH_SCHEDULER_SYNC_FAILED")
            self.stop_event.wait(interval)


def _load_json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def create_workbench_app(settings: Settings, enable_scheduler: bool = True) -> FastAPI:
    db = WorkbenchDB(settings.workbench_db_path)
    db.init_schema()

    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

    stop_event = threading.Event()
    scheduler: _SchedulerThread | None = None

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        nonlocal scheduler
        if enable_scheduler:
            scheduler = _SchedulerThread(settings=settings, stop_event=stop_event)
            scheduler.start()
        try:
            yield
        finally:
            stop_event.set()
            if scheduler and scheduler.is_alive():
                scheduler.join(timeout=2)

    app = FastAPI(title="QQ Mail Workbench", lifespan=_lifespan)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")), name="static")

    search_service = SearchService(
        db=db,
        embedding_client=EmbeddingClient(
            token=settings.github_token,
            api_url=settings.github_embedding_api_url,
            model=settings.workbench_embed_model,
            timeout_seconds=settings.model_request_timeout_seconds,
        ),
        index_path=settings.workbench_faiss_index_path,
        llm_token=settings.github_token,
        llm_api_url=settings.github_api_url,
        llm_model=settings.workbench_llm_model,
    )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        counts = db.count_by_category()
        tasks_open = len(db.list_tasks(status="open"))
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "counts": counts,
                "tasks_open": tasks_open,
                "read_only": settings.workbench_read_only,
            },
        )

    @app.get("/mails", response_class=HTMLResponse)
    def mails(request: Request, category: str | None = Query(default=None)):
        rows = db.list_mails(category=category, limit=200)
        return templates.TemplateResponse(
            request,
            "mails.html",
            {
                "request": request,
                "rows": rows,
                "category": category or "all",
            },
        )

    @app.get("/mail/{mail_id}", response_class=HTMLResponse)
    def mail_detail(request: Request, mail_id: int):
        detail = db.get_mail_detail(mail_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="mail not found")
        mail = detail["mail"]
        evidence = _load_json_list(str(mail["evidence_json"] or "[]"))
        return templates.TemplateResponse(
            request,
            "mail_detail.html",
            {
                "request": request,
                "detail": detail,
                "evidence": evidence,
            },
        )

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks(request: Request, status: str = Query(default="open")):
        rows = db.list_tasks(status=status)
        return templates.TemplateResponse(
            request,
            "tasks.html",
            {
                "request": request,
                "rows": rows,
                "status": status,
            },
        )

    @app.post("/tasks/{task_id}/done")
    def task_done(task_id: int):
        ok = db.mark_task_done(task_id)
        if not ok:
            raise HTTPException(status_code=404, detail="task not found")
        return RedirectResponse(url="/tasks?status=open", status_code=303)

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = Query(default="")):
        result = None
        if q.strip():
            result = search_service.answer_with_evidence(query=q.strip(), top_k=settings.workbench_vector_top_k)
        return templates.TemplateResponse(
            request,
            "search.html",
            {
                "request": request,
                "query": q,
                "result": result,
            },
        )

    @app.get("/attachments/{attachment_id}/download")
    def download_attachment(attachment_id: int):
        with db.session() as conn:
            row = conn.execute("SELECT filename, local_path, download_status FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        if str(row["download_status"]) != "downloaded":
            raise HTTPException(status_code=409, detail="attachment is not downloaded")
        local_path = Path(str(row["local_path"]))
        if not local_path.exists():
            raise HTTPException(status_code=404, detail="file missing")
        return FileResponse(path=local_path, filename=str(row["filename"]))

    @app.post("/sync")
    def sync_once():
        service = SyncService(settings=settings, logger=LOGGER)
        result = service.run_once()
        return {
            "lock_acquired": result.lock_acquired,
            "stats": result.stats.__dict__,
        }

    return app
