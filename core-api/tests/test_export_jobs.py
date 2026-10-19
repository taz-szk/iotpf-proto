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


# ---------------------------------------------------------------------------
# 最終レビュー指摘への回帰テスト
# ---------------------------------------------------------------------------
from app.services.device_groups import GroupNotFoundError
from app.services.export_csv import InfluxUnavailableError
from app.services.export_jobs import run_export_job, UnknownDeviceError

_START = datetime(2026,10,1,tzinfo=timezone.utc)
_END = datetime(2026,10,2,tzinfo=timezone.utc)


def test_queued_job_without_started_at_is_recovered_after_timeout_by_requested_at():
    """スレッドが起動せずstarted_at=Noneのままのqueuedジョブも、requested_atからタイムアウトで復旧できる。"""
    db, existing = _db_with_existing_job("queued", None)
    existing.requested_at = datetime.now(timezone.utc) - timedelta(seconds=700)
    with patch("app.services.export_jobs.estimate_export_row_count", return_value=10), \
         patch("app.services.export_jobs.threading.Thread") as mock_thread:
        job = request_adhoc_export(db, _tenant(), _START, _END, device_ids=None, group_id=None)
    assert job.status == "queued"
    mock_thread.return_value.start.assert_called_once()


def test_recent_queued_job_without_started_at_still_blocks():
    db, existing = _db_with_existing_job("queued", None)
    existing.requested_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    with pytest.raises(JobAlreadyRunningError):
        request_adhoc_export(db, _tenant(), _START, _END, device_ids=None, group_id=None)


def test_request_rejects_device_id_not_belonging_to_tenant():
    evil = 'evil") |> to(bucket: "x")'
    db, existing = _db_with_existing_job("done", datetime.now(timezone.utc))
    with patch("app.services.export_jobs._fetch_existing_device_ids", return_value={"dev-1"}) as mock_fetch, \
         patch("app.services.export_jobs.estimate_export_row_count") as mock_estimate:
        with pytest.raises(UnknownDeviceError) as ei:
            request_adhoc_export(db, _tenant(), _START, _END, device_ids=["dev-1", evil], group_id=None)
    assert ei.value.missing == [evil]
    assert mock_fetch.call_args.args[0] == "tenant_11111111_1111_1111_1111_111111111111"
    mock_estimate.assert_not_called()
    db.commit.assert_not_called()


def test_group_scoped_request_estimates_only_group_devices():
    db, existing = _db_with_existing_job("done", datetime.now(timezone.utc))
    with patch("app.services.export_jobs.list_group_device_ids", return_value=["dev-a", "dev-b"]) as mock_group, \
         patch("app.services.export_jobs.estimate_export_row_count", return_value=1) as mock_estimate, \
         patch("app.services.export_jobs.threading.Thread"):
        request_adhoc_export(db, _tenant(), _START, _END, device_ids=None,
                              group_id="33333333-3333-3333-3333-333333333333")
    mock_group.assert_called_once_with("tenant_11111111_1111_1111_1111_111111111111",
                                       "33333333-3333-3333-3333-333333333333")
    assert mock_estimate.call_args.args[3] == ["dev-a", "dev-b"]


def test_request_with_unknown_group_fails_at_request_time():
    db, existing = _db_with_existing_job("done", datetime.now(timezone.utc))
    with patch("app.services.export_jobs.list_group_device_ids", side_effect=GroupNotFoundError("g")), \
         patch("app.services.export_jobs.threading.Thread") as mock_thread:
        with pytest.raises(GroupNotFoundError):
            request_adhoc_export(db, _tenant(), _START, _END, device_ids=None,
                                  group_id="33333333-3333-3333-3333-333333333333")
    mock_thread.assert_not_called()
    db.commit.assert_not_called()


def test_new_request_keeps_previous_file_until_new_generation_succeeds():
    db, existing = _db_with_existing_job("done", datetime.now(timezone.utc))
    existing.minio_key = f"{TENANT_ID}/job/old"; existing.file_size_bytes = 100
    with patch("app.services.export_jobs.estimate_export_row_count", return_value=1), \
         patch("app.services.export_jobs.threading.Thread"):
        job = request_adhoc_export(db, _tenant(), _START, _END, device_ids=None, group_id=None)
    assert job.status == "queued"
    assert job.minio_key == f"{TENANT_ID}/job/old" and job.file_size_bytes == 100


def _worker_db(job):
    db = MagicMock()
    db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
    db.query.return_value.filter.return_value.first.return_value = job
    return db


def _job_with_old_file():
    job = ExportJob(tenant_id=TENANT_ID, kind="adhoc", status="queued", period_start=_START, period_end=_END)
    job.id = "44444444-4444-4444-4444-444444444444"
    job.minio_key = f"{TENANT_ID}/{job.id}/old"
    job.file_size_bytes = 100
    job.completed_at = datetime(2026,10,1,tzinfo=timezone.utc)
    return job


def test_failed_regeneration_keeps_old_file_downloadable():
    job = _job_with_old_file()
    db = _worker_db(job)
    with patch("app.database.SessionLocal", return_value=db), \
         patch("app.services.export_jobs.generate_export_csv", side_effect=InfluxUnavailableError("boom")), \
         patch("app.services.export_jobs.upload_export_file") as mock_upload, \
         patch("app.services.export_jobs.delete_export_file") as mock_delete:
        run_export_job(str(job.id))
    mock_upload.assert_not_called()
    mock_delete.assert_not_called()
    assert job.status == "done"
    assert job.minio_key == f"{TENANT_ID}/{job.id}/old" and job.file_size_bytes == 100
    assert job.completed_at == datetime(2026,10,1,tzinfo=timezone.utc)
    assert "boom" in job.error_message and "前回のファイル" in job.error_message


def test_failed_first_generation_is_marked_failed():
    job = ExportJob(tenant_id=TENANT_ID, kind="adhoc", status="queued", period_start=_START, period_end=_END)
    job.id = "44444444-4444-4444-4444-444444444444"
    db = _worker_db(job)
    with patch("app.database.SessionLocal", return_value=db), \
         patch("app.services.export_jobs.generate_export_csv", side_effect=InfluxUnavailableError("boom")):
        run_export_job(str(job.id))
    assert job.status == "failed" and job.minio_key is None
    assert job.error_message == "boom"


def test_successful_regeneration_replaces_and_deletes_old_file():
    job = _job_with_old_file()
    job.error_message = "previous attempt failed"
    db = _worker_db(job)
    new_key = f"{TENANT_ID}/{job.id}/new"
    with patch("app.database.SessionLocal", return_value=db), \
         patch("app.services.export_jobs.generate_export_csv", return_value=b"a,b\r\n"), \
         patch("app.services.export_jobs.upload_export_file", return_value=new_key), \
         patch("app.services.export_jobs.delete_export_file") as mock_delete:
        run_export_job(str(job.id))
    assert job.status == "done" and job.minio_key == new_key and job.file_size_bytes == 5
    assert job.error_message is None
    mock_delete.assert_called_once_with(f"{TENANT_ID}/{job.id}/old")
