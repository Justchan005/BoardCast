"""Local mode must keep audit records local despite existing cloud configuration."""
import pytest
from google.cloud import bigquery

from backend import audit, config, store
from backend.localdb import LocalClient


@pytest.mark.parametrize("cached_cloud_client", [False, True])
def test_local_audit_never_contacts_bigquery(tmp_path, monkeypatch, cached_cloud_client):
    monkeypatch.delenv("AUDIT_DISABLED", raising=False)
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "BQ_AUDIT_TABLE", "configured-project.dataset.audit")
    client = LocalClient(tmp_path / "audit.json")
    monkeypatch.setattr(store, "_db", client)
    cloud_calls = []

    class ForbiddenCloudClient:
        def insert_rows_json(self, *args, **kwargs):
            cloud_calls.append("insert")
            raise AssertionError("Local audit must not contact BigQuery")

    def forbidden_factory(*args, **kwargs):
        cloud_calls.append("create")
        raise AssertionError("Local audit must not create a BigQuery client")

    monkeypatch.setattr(audit, "_bq", ForbiddenCloudClient() if cached_cloud_client else None)
    monkeypatch.setattr(bigquery, "Client", forbidden_factory)
    audit.log("rep@mail.apu.edu.my", "submission_test", "item-test", {"status": "pending"})

    assert cloud_calls == []
    rows = [snap.to_dict() for snap in client.collection("audit").stream()]
    assert len(rows) == 1
    assert rows[0]["actor"] == "rep@mail.apu.edu.my"
    assert rows[0]["action"] == "submission_test" and rows[0]["item_id"] == "item-test"
    assert "pending" in rows[0]["detail"]
