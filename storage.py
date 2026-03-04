from __future__ import annotations

import json
import time
from pathlib import Path


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._processed = self._load()

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        if not isinstance(data, list):
            return set()
        return {str(item) for item in data}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(sorted(self._processed), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_processed(self, dedupe_key: str) -> bool:
        return dedupe_key in self._processed

    def mark_processed(self, dedupe_key: str) -> None:
        self._processed.add(dedupe_key)
        self._save()


class AllowlistStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._exact: set[str] = set()
        self._domains: set[str] = set()
        self.reload()

    def reload(self) -> None:
        self._exact = set()
        self._domains = set()
        if not self.path.exists():
            return

        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip().lower()
            if not line or line.startswith("#"):
                continue
            if line.startswith("@"):
                self._domains.add(line[1:])
            else:
                self._exact.add(line)

    def contains(self, sender_email: str) -> bool:
        sender = sender_email.strip().lower()
        if not sender:
            return False
        if sender in self._exact:
            return True
        domain = sender.split("@")[-1] if "@" in sender else ""
        return domain in self._domains


class FrequentSenderStore:
    def __init__(
        self,
        path: Path,
        window_days: int = 30,
        min_count: int = 3,
        max_events: int = 20,
    ) -> None:
        self.path = path
        self.window_days = window_days
        self.min_count = min_count
        self.max_events = max_events
        self._data = self._load()

    def _load(self) -> dict[str, list[int]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}

        data: dict[str, list[int]] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, list):
                continue
            events: list[int] = []
            for item in value:
                if isinstance(item, int):
                    events.append(item)
            data[key.lower().strip()] = events
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _normalize(self, sender_email: str) -> str:
        return sender_email.strip().lower()

    def _prune(self, sender_email: str, now_ts: int) -> list[int]:
        sender = self._normalize(sender_email)
        cutoff = now_ts - self.window_days * 24 * 3600
        history = [ts for ts in self._data.get(sender, []) if ts >= cutoff]
        if len(history) > self.max_events:
            history = history[-self.max_events :]
        self._data[sender] = history
        return history

    def record(self, sender_email: str, ts: int | None = None) -> None:
        sender = self._normalize(sender_email)
        if not sender:
            return
        now_ts = ts if ts is not None else int(time.time())
        history = self._prune(sender, now_ts)
        history.append(now_ts)
        if len(history) > self.max_events:
            history = history[-self.max_events :]
        self._data[sender] = history
        self._save()

    def is_frequent(self, sender_email: str, now_ts: int | None = None) -> bool:
        sender = self._normalize(sender_email)
        if not sender:
            return False
        ts = now_ts if now_ts is not None else int(time.time())
        history = self._prune(sender, ts)
        return len(history) >= self.min_count
