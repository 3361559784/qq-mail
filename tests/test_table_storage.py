from __future__ import annotations

import json
import unittest

from storage import TableFrequentSenderStore, TableProcessedStore, build_row_key


class ResourceExistsError(Exception):
    pass


class ResourceNotFoundError(Exception):
    pass


class FakeTableClient:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], dict] = {}

    def create_entity(self, entity):  # type: ignore[no-untyped-def]
        key = (entity["PartitionKey"], entity["RowKey"])
        if key in self._data:
            raise ResourceExistsError("entity exists")
        self._data[key] = dict(entity)

    def get_entity(self, partition_key, row_key):  # type: ignore[no-untyped-def]
        key = (partition_key, row_key)
        if key not in self._data:
            raise ResourceNotFoundError("not found")
        return dict(self._data[key])

    def upsert_entity(self, entity, mode=None):  # type: ignore[no-untyped-def]
        del mode
        key = (entity["PartitionKey"], entity["RowKey"])
        self._data[key] = dict(entity)


class TestTableStorage(unittest.TestCase):
    def test_row_key_length_32(self) -> None:
        key = build_row_key("some-very-long-dedupe-key-value")
        self.assertEqual(len(key), 32)

    def test_mark_processed_is_idempotent(self) -> None:
        table = FakeTableClient()
        store = TableProcessedStore(table_name="processedstate", table_client=table)

        first = store.mark_processed("dedupe-001", "user@example.com")
        second = store.mark_processed("dedupe-001", "user@example.com")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(store.is_processed("dedupe-001"))

    def test_frequent_sender_window_prune_and_limit(self) -> None:
        table = FakeTableClient()
        store = TableFrequentSenderStore(
            table_name="frequentsenderstate",
            window_days=1,
            min_count=2,
            max_events=3,
            table_client=table,
        )

        sender = "friend@example.com"
        base = 1_800_000_000
        for ts in [base - 90_000, base - 10, base - 9, base - 8, base - 7]:
            store.record(sender, ts=ts)

        row_key = build_row_key(sender)
        entity = table.get_entity("sender", row_key)
        events = json.loads(entity["events_json"])

        # Keep only recent window and last max_events entries.
        self.assertEqual(len(events), 3)
        self.assertEqual(events, [base - 9, base - 8, base - 7])
        self.assertTrue(store.is_frequent(sender, now_ts=base))


if __name__ == "__main__":
    unittest.main()
