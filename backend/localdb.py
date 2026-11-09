"""A tiny stand-in for Firestore, used only when LOCAL_MODE=true (laptop, no Google Cloud).

Implements just the calls BoardCast makes:
    db.collection(name).document(id).get() / .set(data, merge=) / .update(fields)
    db.collection(name).add(data)
    db.collection(name).where(field, "==" | "in", value).order_by(field, direction=).limit(n).stream()
Data is kept in one JSON file (LOCAL_DATA_DIR/db.json) so it survives restarts and you can
open it to see what happened. Never used on Cloud Run.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _encode(v: Any) -> Any:
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, date):
        return {"__d__": v.isoformat()}
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_encode(x) for x in v]
    return v


def _decode(v: Any) -> Any:
    if isinstance(v, dict):
        if set(v) == {"__dt__"}:
            return datetime.fromisoformat(v["__dt__"])
        if set(v) == {"__d__"}:
            return date.fromisoformat(v["__d__"])
        return {k: _decode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


class NotFound(Exception):
    pass


class Snapshot:
    def __init__(self, doc_id: str, data: dict | None):
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return _decode(self._data) if self._data is not None else None


class DocumentRef:
    def __init__(self, client: "LocalClient", collection: str, doc_id: str):
        self._c, self._col, self.id = client, collection, doc_id

    def get(self) -> Snapshot:
        with self._c.lock:
            return Snapshot(self.id, self._c.data.get(self._col, {}).get(self.id))

    def set(self, data: dict, merge: bool = False) -> None:
        with self._c.lock:
            col = self._c.data.setdefault(self._col, {})
            col[self.id] = {**col.get(self.id, {}), **_encode(data)} if merge else _encode(data)
            self._c.save()

    def update(self, fields: dict) -> None:
        with self._c.lock:
            col = self._c.data.setdefault(self._col, {})
            if self.id not in col:
                raise NotFound(f"{self._col}/{self.id} not found")
            col[self.id].update(_encode(fields))
            self._c.save()


class Query:
    def __init__(self, client: "LocalClient", collection: str):
        self._c, self._col = client, collection
        self._filters: list[tuple[str, str, Any]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    def _copy(self) -> "Query":
        q = Query(self._c, self._col)
        q._filters, q._order, q._limit = list(self._filters), self._order, self._limit
        return q

    def where(self, field: str, op: str, value: Any) -> "Query":
        if op not in ("==", "in"):
            raise NotImplementedError(f"localdb supports == and in, not {op}")
        q = self._copy()
        q._filters.append((field, op, value))
        return q

    def order_by(self, field: str, direction: str = "ASCENDING") -> "Query":
        q = self._copy()
        q._order = (field, direction.upper().startswith("DESC"))
        return q

    def limit(self, n: int) -> "Query":
        q = self._copy()
        q._limit = n
        return q

    def stream(self):
        with self._c.lock:
            rows = list(self._c.data.get(self._col, {}).items())
        out = []
        for doc_id, raw in rows:
            doc = _decode(raw)
            if all((doc.get(f) == v) if op == "==" else (doc.get(f) in v) for f, op, v in self._filters):
                out.append((doc_id, raw, doc))
        if self._order:
            field, desc = self._order
            out.sort(key=lambda r: str(r[2].get(field) or ""), reverse=desc)
        if self._limit is not None:
            out = out[: self._limit]
        return iter([Snapshot(doc_id, raw) for doc_id, raw, _ in out])


class CollectionRef(Query):
    def document(self, doc_id: str) -> DocumentRef:
        return DocumentRef(self._c, self._col, doc_id)

    def add(self, data: dict) -> tuple[None, DocumentRef]:
        ref = self.document(uuid.uuid4().hex[:20])
        ref.set(data)
        return None, ref


class LocalClient:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, dict[str, dict]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})

    def collection(self, name: str) -> CollectionRef:
        return CollectionRef(self, name)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, default=str), encoding="utf-8")
        tmp.replace(self.path)
