from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:
    from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
    from azure.data.tables import TableServiceClient, UpdateMode
except Exception:  # pragma: no cover - optional dependency for local file mode
    ResourceExistsError = None  # type: ignore[assignment]
    ResourceNotFoundError = None  # type: ignore[assignment]
    TableServiceClient = None  # type: ignore[assignment]
    UpdateMode = None  # type: ignore[assignment]


def build_row_key(value: str) -> str:
    # Keep RowKey compact for better table index performance.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _utc_iso(ts: int | None = None) -> str:
    t = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return t.isoformat()


def _is_exists_error(exc: Exception) -> bool:
    if ResourceExistsError is not None and isinstance(exc, ResourceExistsError):
        return True
    return exc.__class__.__name__ == "ResourceExistsError"


def _is_not_found_error(exc: Exception) -> bool:
    if ResourceNotFoundError is not None and isinstance(exc, ResourceNotFoundError):
        return True
    return exc.__class__.__name__ == "ResourceNotFoundError"


def resolve_table_connection_string(explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    return os.getenv("AzureWebJobsStorage", "").strip()


class ProcessedStore(Protocol):
    def is_processed(self, dedupe_key: str) -> bool: ...

    def claim_processing(self, dedupe_key: str, sender_email: str, ttl_seconds: int = 1800) -> bool: ...

    def mark_processed(self, dedupe_key: str, sender_email: str) -> bool: ...

    def clear_processing(self, dedupe_key: str) -> None: ...


class FrequentStore(Protocol):
    def record(self, sender_email: str, ts: int | None = None) -> None: ...

    def is_frequent(self, sender_email: str, now_ts: int | None = None) -> bool: ...


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._processed = self._load()
        self._processing: set[str] = set()

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

    def claim_processing(self, dedupe_key: str, sender_email: str, ttl_seconds: int = 1800) -> bool:
        del sender_email, ttl_seconds
        if dedupe_key in self._processed:
            return False
        if dedupe_key in self._processing:
            return False
        self._processing.add(dedupe_key)
        return True

    def mark_processed(self, dedupe_key: str, sender_email: str) -> bool:
        del sender_email
        if dedupe_key in self._processed:
            return False
        self._processing.discard(dedupe_key)
        self._processed.add(dedupe_key)
        self._save()
        return True

    def clear_processing(self, dedupe_key: str) -> None:
        self._processing.discard(dedupe_key)


class TableProcessedStore:
    PARTITION_KEY = "processed"
    STATUS_PROCESSING = "processing"
    STATUS_PROCESSED = "processed"
    DEFAULT_PROCESSING_TTL_SECONDS = 1800

    def __init__(
        self,
        table_name: str,
        connection_string: str = "",
        table_client: Any | None = None,
    ) -> None:
        if table_client is not None:
            self._table = table_client
            return

        if TableServiceClient is None:
            raise RuntimeError("azure-data-tables is not installed")
        if not connection_string.strip():
            raise ValueError("Table connection string is required for table backend")

        service = TableServiceClient.from_connection_string(connection_string)
        service.create_table_if_not_exists(table_name=table_name)
        self._table = service.get_table_client(table_name=table_name)

    @staticmethod
    def _now_ts() -> int:
        return int(time.time())

    def is_processed(self, dedupe_key: str) -> bool:
        row_key = build_row_key(dedupe_key)
        try:
            entity = self._table.get_entity(partition_key=self.PARTITION_KEY, row_key=row_key)
            status = str(entity.get("status", self.STATUS_PROCESSED))
            if status == self.STATUS_PROCESSED:
                return True
            if status == self.STATUS_PROCESSING:
                expires_at = int(entity.get("processing_expires_at", 0) or 0)
                if expires_at and expires_at < self._now_ts():
                    return False
                return False
            return False
        except Exception as exc:
            if _is_not_found_error(exc):
                return False
            raise

    def claim_processing(self, dedupe_key: str, sender_email: str, ttl_seconds: int = DEFAULT_PROCESSING_TTL_SECONDS) -> bool:
        row_key = build_row_key(dedupe_key)
        now_ts = self._now_ts()
        expires_at = now_ts + max(ttl_seconds, 60)
        entity = {
            "PartitionKey": self.PARTITION_KEY,
            "RowKey": row_key,
            "dedupe_key": dedupe_key,
            "sender_email": sender_email.lower().strip(),
            "status": self.STATUS_PROCESSING,
            "processing_expires_at": expires_at,
            "processed_at_utc": None,
        }
        try:
            self._table.create_entity(entity=entity)
            return True
        except Exception as exc:
            if not _is_exists_error(exc):
                raise

            try:
                existing = self._table.get_entity(partition_key=self.PARTITION_KEY, row_key=row_key)
            except Exception as get_exc:
                if _is_not_found_error(get_exc):
                    # Concurrent delete between create and get; retry claim.
                    self._table.create_entity(entity=entity)
                    return True
                raise

            status = str(existing.get("status", self.STATUS_PROCESSED))
            if status == self.STATUS_PROCESSED:
                return False

            expires = int(existing.get("processing_expires_at", 0) or 0)
            if expires and expires > now_ts:
                return False

            entity["processing_expires_at"] = expires_at
            try:
                self._table.upsert_entity(entity=entity, mode=UpdateMode.REPLACE if UpdateMode else None)
            except TypeError:
                # UpdateMode may be None when azure dependency is not installed; ignore mode.
                self._table.upsert_entity(entity=entity)
            return True

    def mark_processed(self, dedupe_key: str, sender_email: str) -> bool:
        row_key = build_row_key(dedupe_key)
        entity = {
            "PartitionKey": self.PARTITION_KEY,
            "RowKey": row_key,
            "dedupe_key": dedupe_key,
            "sender_email": sender_email.lower().strip(),
            "status": self.STATUS_PROCESSED,
            "processing_expires_at": None,
            "processed_at_utc": _utc_iso(),
        }
        try:
            self._table.create_entity(entity=entity)
            return True
        except Exception as exc:
            if not _is_exists_error(exc):
                raise

            try:
                existing = self._table.get_entity(partition_key=self.PARTITION_KEY, row_key=row_key)
            except Exception as get_exc:
                if _is_not_found_error(get_exc):
                    self._table.create_entity(entity=entity)
                    return True
                raise

            status = str(existing.get("status", self.STATUS_PROCESSED))
            if status == self.STATUS_PROCESSED:
                return False

            try:
                self._table.upsert_entity(entity=entity, mode=UpdateMode.REPLACE if UpdateMode else None)
            except TypeError:
                self._table.upsert_entity(entity=entity)
            return True

    def clear_processing(self, dedupe_key: str) -> None:
        row_key = build_row_key(dedupe_key)
        try:
            entity = self._table.get_entity(partition_key=self.PARTITION_KEY, row_key=row_key)
        except Exception as exc:
            if _is_not_found_error(exc):
                return
            raise

        status = str(entity.get("status", self.STATUS_PROCESSED))
        if status != self.STATUS_PROCESSING:
            return

        try:
            self._table.delete_entity(partition_key=self.PARTITION_KEY, row_key=row_key)
        except Exception as exc:
            if _is_not_found_error(exc):
                return
            raise


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


class DenylistStore:
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


class TableFrequentSenderStore:
    PARTITION_KEY = "sender"

    def __init__(
        self,
        table_name: str,
        window_days: int = 30,
        min_count: int = 3,
        max_events: int = 20,
        connection_string: str = "",
        table_client: Any | None = None,
    ) -> None:
        self.window_days = window_days
        self.min_count = min_count
        self.max_events = max_events

        if table_client is not None:
            self._table = table_client
            return

        if TableServiceClient is None:
            raise RuntimeError("azure-data-tables is not installed")
        if not connection_string.strip():
            raise ValueError("Table connection string is required for table backend")

        service = TableServiceClient.from_connection_string(connection_string)
        service.create_table_if_not_exists(table_name=table_name)
        self._table = service.get_table_client(table_name=table_name)

    @staticmethod
    def _normalize(sender_email: str) -> str:
        return sender_email.strip().lower()

    def _row_key(self, sender_email: str) -> str:
        return build_row_key(self._normalize(sender_email))

    def _get_entity(self, sender_email: str) -> dict[str, Any] | None:
        try:
            return self._table.get_entity(
                partition_key=self.PARTITION_KEY,
                row_key=self._row_key(sender_email),
            )
        except Exception as exc:
            if _is_not_found_error(exc):
                return None
            raise

    @staticmethod
    def _parse_events(raw: Any) -> list[int]:
        if not raw:
            return []
        if isinstance(raw, list):
            return [int(item) for item in raw if isinstance(item, int)]
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except Exception:
                return []
            if not isinstance(data, list):
                return []
            return [int(item) for item in data if isinstance(item, int)]
        return []

    def _prune(self, events: list[int], now_ts: int) -> list[int]:
        cutoff = now_ts - self.window_days * 24 * 3600
        kept = [ts for ts in events if ts >= cutoff]
        if len(kept) > self.max_events:
            kept = kept[-self.max_events :]
        return kept

    def _write_events(self, sender_email: str, events: list[int], now_ts: int) -> None:
        entity = {
            "PartitionKey": self.PARTITION_KEY,
            "RowKey": self._row_key(sender_email),
            "sender_email": self._normalize(sender_email),
            "events_json": json.dumps(events, ensure_ascii=False),
            "updated_at_utc": _utc_iso(now_ts),
        }
        if UpdateMode is None:
            self._table.upsert_entity(entity=entity)
            return
        self._table.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

    def record(self, sender_email: str, ts: int | None = None) -> None:
        sender = self._normalize(sender_email)
        if not sender:
            return
        now_ts = ts if ts is not None else int(time.time())
        entity = self._get_entity(sender)
        events = self._parse_events(entity.get("events_json") if entity else [])
        events = self._prune(events, now_ts)
        events.append(now_ts)
        if len(events) > self.max_events:
            events = events[-self.max_events :]
        self._write_events(sender, events, now_ts)

    def is_frequent(self, sender_email: str, now_ts: int | None = None) -> bool:
        sender = self._normalize(sender_email)
        if not sender:
            return False
        ts = now_ts if now_ts is not None else int(time.time())
        entity = self._get_entity(sender)
        if not entity:
            return False
        events = self._parse_events(entity.get("events_json"))
        pruned = self._prune(events, ts)
        if pruned != events:
            self._write_events(sender, pruned, ts)
        return len(pruned) >= self.min_count
