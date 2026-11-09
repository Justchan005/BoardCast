"""Firestore access. Collections:

roles/{email}        {role, club_id}
items/{id}           Item (see shared/schemas.py)
subscribers/{email}  Subscriber
issues/{id}          {week_start, status, html, item_ids, sent_at}
clicks/{auto}        {item_id, kind, ts}
saved/{email|item}   {email, item_id, via, ts, reminded}   events a student registered for / added
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.cloud import firestore

from . import config

_db: Optional[firestore.Client] = None


def db() -> firestore.Client:
    global _db
    if _db is None:
        if config.LOCAL_MODE:
            from .localdb import LocalClient
            _db = LocalClient(config.LOCAL_DATA_DIR / "db.json")
        else:
            _db = firestore.Client(project=config.PROJECT_ID or None)
    return _db


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- roles ----------

def get_role(email: str) -> Optional[dict]:
    snap = db().collection("roles").document(email.lower()).get()
    return snap.to_dict() if snap.exists else None


# ---------- items ----------

def _next_item_version(current: dict | None) -> datetime:
    """A new review version even when the wall clock repeats or moves backwards."""
    timestamp = now()
    previous = (current or {}).get("updated_at")
    if isinstance(previous, datetime):
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        timestamp = max(timestamp, previous + timedelta(microseconds=1))
    return timestamp


def _mutate_item(item_id: str, transform, *, replace: bool = False) -> dict | None:
    """Choose a strictly newer version inside the same lock/transaction as its write."""
    client = db()
    ref = client.collection("items").document(item_id)

    def mutate(transaction=None):
        current = (ref.get(transaction=transaction) if transaction is not None else ref.get()).to_dict()
        fields = transform(current)
        if fields is None:
            return None
        fields = {**fields, "updated_at": _next_item_version(current)}
        if replace:
            fields["created_at"] = fields["updated_at"]
        if transaction is not None:
            if replace:
                transaction.set(ref, fields)
            else:
                transaction.update(ref, fields)
        elif replace:
            ref.set(fields)
        else:
            ref.update(fields)
        return fields if replace else {**(current or {}), **fields}

    if config.LOCAL_MODE:
        with client.lock:
            return mutate()
    return firestore.transactional(mutate)(client.transaction())


def create_item(item: dict) -> str:
    item_id = item.get("id") or uuid.uuid4().hex[:12]
    saved = _mutate_item(item_id, lambda current: {**item, "id": item_id}, replace=True)
    item.update(saved)
    return item_id


def get_item(item_id: str) -> Optional[dict]:
    snap = db().collection("items").document(item_id).get()
    return snap.to_dict() if snap.exists else None


def update_item(item_id: str, **fields) -> None:
    _mutate_item(item_id, lambda current: fields)


def update_item_if_unchanged(item: dict, **fields) -> bool:
    """Atomically update the version we read; never overwrite a concurrent edit or decision."""
    return _mutate_item(item["id"], lambda current: fields if current == item else None) is not None


def publish_if_unchanged(item: dict) -> bool:
    """Mark the approved version we sent as published, without undoing a later edit/rejection."""
    return item.get("status") == "approved" and update_item_if_unchanged(item, status="published")


def list_items(statuses: list[str], item_type: Optional[str] = None) -> list[dict]:
    q = db().collection("items").where("status", "in", statuses)
    if item_type:
        q = q.where("type", "==", item_type)
    return [d.to_dict() for d in q.stream()]


# ---------- subscribers ----------

def active_subscribers() -> list[dict]:
    return [d.to_dict() for d in db().collection("subscribers").where("active", "==", True).stream()]


def upsert_subscriber(sub: dict) -> None:
    db().collection("subscribers").document(sub["email"].lower()).set(sub, merge=True)


# ---------- issues + clicks ----------

def save_issue(issue: dict) -> str:
    issue_id = issue.get("id") or uuid.uuid4().hex[:10]
    issue["id"] = issue_id
    issue.setdefault("created_at", now())
    db().collection("issues").document(issue_id).set(issue)
    return issue_id


def get_issue(issue_id: str) -> Optional[dict]:
    snap = db().collection("issues").document(issue_id).get()
    return snap.to_dict() if snap.exists else None


def atomic_documents(keys: list[tuple[str, str]], transform) -> dict | None:
    """Read/check/write related documents atomically, including the local demo store.

    ``transform`` must be side-effect free: Firestore may call it again on conflict.
    It returns partial document updates, or None to decline the operation.
    """
    client = db()
    refs = {key: client.collection(key[0]).document(key[1]) for key in keys}

    def apply(transaction=None):
        rows = {key: (ref.get(transaction=transaction) if transaction is not None else ref.get()).to_dict()
                for key, ref in refs.items()}
        changes = transform(rows)
        if changes is None:
            return None
        result = dict(rows)
        for key, fields in changes.items():
            if transaction is not None:
                transaction.set(refs[key], fields, merge=True)
            else:
                refs[key].set(fields, merge=True)
            result[key] = {**(rows[key] or {}), **fields}
        return result

    if config.LOCAL_MODE:
        with client.lock:
            return apply()
    return firestore.transactional(apply)(client.transaction())


def hold_issue(issue_id: str) -> dict:
    """Stop new recipient attempts; an email already accepted by Gmail cannot be recalled."""
    from fastapi import HTTPException
    key = ("issues", issue_id)

    def hold(rows):
        issue = rows[key]
        if not issue:
            raise HTTPException(404, "Issue not found")
        if issue.get("status") in ("sent", "superseded"):
            raise HTTPException(409, "This issue has already finished; it cannot be held.")
        return {key: {"status": "held", "held_at": now()}}

    return atomic_documents([key], hold)[key]


def log_click(item_id: str, kind: str) -> None:
    db().collection("clicks").add({"item_id": item_id, "kind": kind, "ts": now()})


# ---------- saved events (for 1-hour reminders) ----------

def _saved_id(email: str, item_id: str) -> str:
    return f"{email.lower()}|{item_id}".replace("/", "_")


def save_event(email: str, item_id: str, via: str) -> bool:
    """Remember that a student registered for / added an event. Returns True if new.
    Saving twice keeps the first record, so a reminder is never sent twice."""
    ref = db().collection("saved").document(_saved_id(email, item_id))
    if ref.get().exists:
        return False
    ref.set({"email": email.lower(), "item_id": item_id, "via": via, "ts": now(), "reminded": False})
    return True


def unreminded_saves() -> list[dict]:
    return [{"id": d.id, **d.to_dict()} for d in db().collection("saved").where("reminded", "==", False).stream()]


def mark_reminded(saved_id: str) -> None:
    db().collection("saved").document(saved_id).update({"reminded": True, "reminded_at": now()})


def get_subscriber(email: str) -> Optional[dict]:
    snap = db().collection("subscribers").document(email.lower()).get()
    return snap.to_dict() if snap.exists else None
