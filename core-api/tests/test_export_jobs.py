from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
import pytest
from app.models.public import Tenant
from app.models.export import ExportJob
from app.services.export_jobs import request_adhoc_export, JobAlreadyRunningError, ExportTooLargeError

TENANT_ID = "11111111-1111-1111-1111-111111111111"

def _tenant():
    t = Tenant(); t.id = TENANT_ID; t.influxdb_org_id = "org-1"; t.influxdb_token = "tok"
    return t

def _db_with_existing_job(status, started_at):
    db = MagicMock()
    existing = ExportJob(tenant_id=TENANT_ID, kind="adhoc", status=status,
                          period_start=datetime(2026,10,1,tzinfo=timezone.utc),
                          period_end=datetime(2026,10,2,tzinfo=timezone.utc))
    existing.started_at = started_at
    db.query.return_value.filter.return_value.first.return_value = existing
    return db, existing

def test_request_adhoc_export_rejects_when_already_processing():
    db, _ = _db_with_existing_job("processing", datetime.now(timezone.utc))
    with pytest.raises(JobAlreadyRunningError):
        request_adhoc_export(db, _tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                              datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None, group_id=None)

def test_request_adhoc_export_recovers_stale_lock_past_timeout():
    stale_start = datetime.now(timezone.utc) - timedelta(seconds=700)
    db, existing = _db_with_existing_job("processing", stale_start)
    with patch("app.services.export_jobs.estimate_export_row_count", return_value=10), \
         patch("app.services.export_jobs.threading.Thread") as mock_thread:
        job = request_adhoc_export(db, _tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                    datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None, group_id=None)
    assert job.status == "queued"
    mock_thread.return_value.start.assert_called_once()

def test_request_adhoc_export_rejects_when_estimate_exceeds_limit():
    db, existing = _db_with_existing_job("done", datetime.now(timezone.utc))
    with patch("app.services.export_jobs.estimate_export_row_count",
               return_value=1_000_000) as mock_estimate:
        with pytest.raises(ExportTooLargeError):
            request_adhoc_export(db, _tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                  datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None, group_id=None)
    assert existing.status == "done"  # 既存行は変更されない

def test_request_adhoc_export_rejects_too_large_even_when_lock_is_stale():
    """stale lock(タイムアウト超過で復旧可能)とサイズ超過が同時に発生した場合でも、
    サイズ超過が優先して拒否され、既存行は一切変更されないことを確認する。"""
    stale_start = datetime.now(timezone.utc) - timedelta(seconds=700)
    db, existing = _db_with_existing_job("processing", stale_start)
    with patch("app.services.export_jobs.estimate_export_row_count",
               return_value=1_000_000) as mock_estimate:
        with pytest.raises(ExportTooLargeError):
            request_adhoc_export(db, _tenant(), datetime(2026,10,1,tzinfo=timezone.utc),
                                  datetime(2026,10,2,tzinfo=timezone.utc), device_ids=None, group_id=None)
    assert existing.status == "processing"  # 既存行は変更されない
    assert existing.started_at == stale_start
    assert existing.error_message is None
    db.commit.assert_not_called()
