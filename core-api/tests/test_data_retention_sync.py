from unittest.mock import patch, MagicMock

from app.services.data_retention import sync_tenant_retention, run_daily_retention_sync


def test_sync_tenant_retention_noop_when_bucket_not_found():
    mock_get_resp = MagicMock(status_code=200)
    mock_get_resp.json.return_value = {"buckets": []}
    with patch("app.services.data_retention.httpx.get", return_value=mock_get_resp), \
         patch("app.services.data_retention.httpx.patch") as mock_patch:
        sync_tenant_retention("org-1", "admin-token", 180)
    mock_patch.assert_not_called()


def test_sync_tenant_retention_noop_when_already_matching():
    mock_get_resp = MagicMock(status_code=200)
    mock_get_resp.json.return_value = {
        "buckets": [{"id": "bucket-1", "retentionRules": [{"type": "expire", "everySeconds": 180 * 86400}]}]
    }
    with patch("app.services.data_retention.httpx.get", return_value=mock_get_resp), \
         patch("app.services.data_retention.httpx.patch") as mock_patch:
        sync_tenant_retention("org-1", "admin-token", 180)
    mock_patch.assert_not_called()


def test_sync_tenant_retention_patches_when_different():
    mock_get_resp = MagicMock(status_code=200)
    mock_get_resp.json.return_value = {
        "buckets": [{"id": "bucket-1", "retentionRules": [{"type": "expire", "everySeconds": 365 * 86400}]}]
    }
    mock_patch_resp = MagicMock(status_code=200)
    with patch("app.services.data_retention.httpx.get", return_value=mock_get_resp), \
         patch("app.services.data_retention.httpx.patch", return_value=mock_patch_resp) as mock_patch:
        sync_tenant_retention("org-1", "admin-token", 180)
    mock_patch.assert_called_once()
    call = mock_patch.call_args
    assert "bucket-1" in call.args[0]
    assert call.kwargs["json"]["retentionRules"] == [{"type": "expire", "everySeconds": 180 * 86400}]


def test_sync_tenant_retention_patches_when_no_existing_rule():
    mock_get_resp = MagicMock(status_code=200)
    mock_get_resp.json.return_value = {"buckets": [{"id": "bucket-1", "retentionRules": []}]}
    mock_patch_resp = MagicMock(status_code=200)
    with patch("app.services.data_retention.httpx.get", return_value=mock_get_resp), \
         patch("app.services.data_retention.httpx.patch", return_value=mock_patch_resp) as mock_patch:
        sync_tenant_retention("org-1", "admin-token", 180)
    mock_patch.assert_called_once()


def test_run_daily_retention_sync_processes_active_tenants_with_influxdb_org():
    tenant_with_org = MagicMock(id="tenant-1", status="active", influxdb_org_id="org-1", data_retention_days=None)
    tenant_without_org = MagicMock(id="tenant-2", status="active", influxdb_org_id=None, data_retention_days=None)

    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.all.return_value = [tenant_with_org, tenant_without_org]

    with patch("app.services.data_retention.SessionLocal", return_value=mock_db), \
         patch("app.services.data_retention.get_effective_retention_days", return_value=365), \
         patch("app.services.data_retention.sync_tenant_retention") as mock_sync:
        results = run_daily_retention_sync()

    mock_sync.assert_called_once()
    assert mock_sync.call_args[0][0] == "org-1"
    assert mock_sync.call_args[0][2] == 365
    assert results == [{"tenant_id": "tenant-1", "status": "ok"}]


def test_run_daily_retention_sync_records_error_without_stopping():
    tenant = MagicMock(id="tenant-1", status="active", influxdb_org_id="org-1", data_retention_days=None)

    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.all.return_value = [tenant]

    with patch("app.services.data_retention.SessionLocal", return_value=mock_db), \
         patch("app.services.data_retention.get_effective_retention_days", return_value=365), \
         patch("app.services.data_retention.sync_tenant_retention", side_effect=Exception("influxdb down")):
        results = run_daily_retention_sync()

    assert results == [{"tenant_id": "tenant-1", "status": "error", "detail": "influxdb down"}]
