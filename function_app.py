from __future__ import annotations

import logging
import os

import azure.functions as func

from config import load_settings
from runner import run_once


LOGGER = logging.getLogger("qq-auto-reply")
app = func.FunctionApp()


@app.schedule(
    schedule=os.getenv("TIMER_SCHEDULE", "0 */5 * * * *"),
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def qq_mail_timer(timer: func.TimerRequest) -> None:
    del timer
    settings = load_settings()
    stats = run_once(settings=settings, logger=LOGGER)
    LOGGER.info(
        "Function cycle done fetched=%d replied=%d skipped=%d errors=%d",
        stats.fetched,
        stats.replied,
        stats.skipped,
        stats.errors,
    )
