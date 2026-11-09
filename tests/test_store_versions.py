"""Review versions must advance even on a coarse or backwards wall clock."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import config, store
from backend.localdb import LocalClient


@pytest.fixture
def fixed_clock(tmp_path, monkeypatch):
    instant = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(store, "_db", LocalClient(tmp_path / "db.json"))
    monkeypatch.setattr(store, "now", lambda: instant)
    return instant


def test_every_item_write_advances_version_with_frozen_or_backwards_clock(fixed_clock, monkeypatch):
    store.create_item({"id": "item", "status": "pending", "data": {"title": "Original"}})
    initial = store.get_item("item")
    store.update_item("item", data={"title": "Edited"})
    edited = store.get_item("item")
    assert edited["updated_at"] == initial["updated_at"] + timedelta(microseconds=1)
    assert not store.update_item_if_unchanged(initial, status="approved")
    monkeypatch.setattr(store, "now", lambda: fixed_clock - timedelta(days=1))
    assert store.update_item_if_unchanged(edited, status="approved")
    approved = store.get_item("item")
    assert approved["updated_at"] == edited["updated_at"] + timedelta(microseconds=1)
    store.create_item({"id": "item", "status": "pending", "data": {"title": "Replacement"}})
    assert store.get_item("item")["updated_at"] > approved["updated_at"]


def test_concurrent_item_mutations_get_distinct_versions(fixed_clock):
    store.create_item({"id": "item", "status": "pending"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda number: store.update_item("item", last_writer=number), range(20)))
    assert store.get_item("item")["updated_at"] == fixed_clock + timedelta(microseconds=20)


@pytest.mark.parametrize("operation", ["create", "update", "compare_and_set", "related_documents"])
def test_falsey_firestore_transaction_still_owns_all_reads_and_writes(fixed_clock, monkeypatch, operation):
    # This is an actual empty SDK transaction: __len__ makes it falsey before its
    # first write. No Firestore client, credentials or network calls are involved.
    from google.cloud.firestore_v1.transaction import Transaction
    transaction = Transaction(None)
    assert transaction is not None and not transaction
    transaction.set, transaction.update = Mock(), Mock()
    current = {"id": "item", "status": "pending", "updated_at": fixed_clock}
    ref = Mock()
    ref.get.return_value.to_dict.return_value = None if operation == "create" else current
    client = SimpleNamespace(collection=lambda name: SimpleNamespace(document=lambda item_id: ref),
                             transaction=lambda: transaction)
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    monkeypatch.setattr(store, "_db", client)
    monkeypatch.setattr(store.firestore, "transactional", lambda function: function)

    if operation == "create":
        store.create_item({"id": "item", "status": "pending"})
    elif operation == "update":
        store.update_item("item", status="approved")
    elif operation == "compare_and_set":
        assert store.update_item_if_unchanged(current, status="approved")
    else:
        key = ("items", "item")
        store.atomic_documents([key], lambda rows: {key: {"status": "approved"}})

    ref.get.assert_called_once_with(transaction=transaction)
    ref.set.assert_not_called()
    ref.update.assert_not_called()
    assert transaction.set.call_count + transaction.update.call_count == 1
