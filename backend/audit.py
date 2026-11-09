"""Audit trail: who did what, when. BigQuery if configured, Firestore otherwise.
Logging never breaks the main action. Set AUDIT_DISABLED=true for offline runs (eval, tests)."""
from __future__ import annotations

import logging
import os

from . import config

_bq = None
log_ = logging.getLogger("boardcast.audit")


def log(actor: str, action: str, item_id: str | None = None, detail: dict | None = None) -> None:
    if os.getenv("AUDIT_DISABLED", "").lower() == "true":
        return
    from . import store  # imported lazily so offline tools don't need Firestore
    row = {"ts": store.now().isoformat(), "actor": actor, "action": action,
           "item_id": item_id, "detail": str(detail or {})}
    try:
        if config.BQ_AUDIT_TABLE and not config.LOCAL_MODE:
            global _bq
            from google.cloud import bigquery
            _bq = _bq or bigquery.Client(project=config.PROJECT_ID or None)
            if not _bq.insert_rows_json(config.BQ_AUDIT_TABLE, [row]):
                return
        store.db().collection("audit").add(row)
    except Exception as e:
        log_.warning("audit log failed: %s %s", row, e)
