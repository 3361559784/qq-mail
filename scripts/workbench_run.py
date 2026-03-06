#!/usr/bin/env python3
from __future__ import annotations

import logging

import uvicorn

from config import load_settings
from workbench.web_app import create_workbench_app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    settings = load_settings()
    app = create_workbench_app(settings=settings, enable_scheduler=True)
    uvicorn.run(app, host="127.0.0.1", port=8787)
