#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import time

from config import load_settings
from runner import run_once


LOGGER = logging.getLogger("qq-auto-reply")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QQ Mail auto-reply bot powered by GitHub Models")
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    return parser.parse_args()


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

        if args.once:
            break
        time.sleep(max(settings.poll_seconds, 5))


if __name__ == "__main__":
    main()
