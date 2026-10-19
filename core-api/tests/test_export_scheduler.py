from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from app.models.export import ExportJob
from app.services.export_csv import InfluxUnavailableError
from app.services.export_scheduler import (
    run_due_periodic_exports, PERIODIC_TOO_LARGE_MESSAGE,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _schedule(frequency, last_run_at):
    s = MagicMock(tenant_id=TENANT_ID, frequency=frequency,
                  device_ids=None, group_id=None, enabled=True, last_run_at=last_run_at)
    return s


def _db(schedules, job=None):
    db = MagicMock()
    db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
    db.query.return_value.filter.return_value.all.return_value = schedules
    if job is not None:
        db.query.return_value.filter.return_value.first.return_value = job
    return db


def test_run_due_periodic_exports_skips_not_yet_due():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    db = _db([_schedule("daily", recent)])
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.estimate_export_row_count", return_value=0), \
         patch("app.services.export_scheduler.run_export_job") as mock_run:
        run_due_periodic_exports()
    mock_run.assert_not_called()


def test_run_due_periodic_exports_runs_when_due():
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db = _db([_schedule("daily", old)])
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.estimate_export_row_count", return_value=10), \
         patch("app.services.export_scheduler.run_export_job") as mock_run:
        run_due_periodic_exports()
    mock_run.assert_called_once()


def test_run_due_periodic_exports_does_not_use_adhoc_lock():
    """回帰テスト: 定期実行はアドホック用のrequest_adhoc_export(ロック付き)を経由しない。
    (サイズ上限チェックは定期実行にも適用するようになったため、ここでは検査しない。)"""
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db = _db([_schedule("daily", old)])
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.estimate_export_row_count", return_value=10), \
         patch("app.services.export_scheduler.run_export_job") as mock_run, \
         patch("app.services.export_scheduler.request_adhoc_export", create=True) as mock_adhoc_local, \
         patch("app.services.export_jobs.request_adhoc_export") as mock_adhoc_source:
        run_due_periodic_exports()
    mock_adhoc_local.assert_not_called()
    mock_adhoc_source.assert_not_called()
    mock_run.assert_called_once()


def test_run_due_periodic_exports_calls_run_export_job_synchronously():
    """回帰テスト: run_export_jobが新規スレッドを立てず同期呼び出しされることを確認する。"""
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db = _db([_schedule("daily", old)])
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.estimate_export_row_count", return_value=10), \
         patch("app.services.export_scheduler.run_export_job") as mock_run, \
         patch("threading.Thread") as mock_thread:
        run_due_periodic_exports()
    mock_run.assert_called_once()
    mock_thread.assert_not_called()


class _FrozenDatetime(datetime):
    current: datetime = None

    @classmethod
    def now(cls, tz=None):
        return cls.current


def test_consecutive_runs_produce_contiguous_windows():
    """スケジューラは1時間おきのポーリングなので、2回目の実行は前回から interval+α 後になる。
    2回目の期間の開始は1回目の期間の終端と一致し、隙間が無いこと。"""
    schedule = SimpleNamespace(tenant_id=TENANT_ID, frequency="daily", device_ids=None,
                               group_id=None, enabled=True, last_run_at=None)
    job = ExportJob(tenant_id=TENANT_ID, kind="periodic")
    db = _db([schedule], job=job)

    t0 = datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1, minutes=59)  # 次のポーリングで初めて期限到来
    windows = []
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.datetime", _FrozenDatetime), \
         patch("app.services.export_scheduler.estimate_export_row_count", return_value=10), \
         patch("app.services.export_scheduler.run_export_job"):
        for t in (t0, t1):
            _FrozenDatetime.current = t
            run_due_periodic_exports()
            windows.append((job.period_start, job.period_end))

    assert windows[0] == (t0 - timedelta(days=1), t0)
    assert windows[1] == (t0, t1)
    assert windows[1][0] == windows[0][1]  # 隙間なし
    assert schedule.last_run_at == t1


def test_due_schedule_exceeding_size_limit_is_recorded_as_failed_without_generating():
    schedule = SimpleNamespace(tenant_id=TENANT_ID, frequency="daily", device_ids=None,
                               group_id=None, enabled=True,
                               last_run_at=datetime.now(timezone.utc) - timedelta(days=2))
    job = ExportJob(tenant_id=TENANT_ID, kind="periodic")
    db = _db([schedule], job=job)
    before = datetime.now(timezone.utc)
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.estimate_export_row_count", return_value=10_000_000), \
         patch("app.services.export_scheduler.run_export_job") as mock_run, \
         patch("app.services.export_jobs.generate_export_csv") as mock_generate:
        run_due_periodic_exports()
    mock_run.assert_not_called()
    mock_generate.assert_not_called()
    assert job.status == "failed"
    assert job.error_message == PERIODIC_TOO_LARGE_MESSAGE
    assert schedule.last_run_at >= before  # 次の周期まで再試行しない


def test_due_schedule_exceeding_size_limit_keeps_previous_file_downloadable():
    schedule = SimpleNamespace(tenant_id=TENANT_ID, frequency="daily", device_ids=None,
                               group_id=None, enabled=True,
                               last_run_at=datetime.now(timezone.utc) - timedelta(days=2))
    job = ExportJob(tenant_id=TENANT_ID, kind="periodic", status="done",
                    minio_key=f"{TENANT_ID}/job/old", file_size_bytes=123)
    db = _db([schedule], job=job)
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.estimate_export_row_count", return_value=10_000_000), \
         patch("app.services.export_scheduler.run_export_job") as mock_run:
        run_due_periodic_exports()
    mock_run.assert_not_called()
    assert job.status == "done"
    assert job.minio_key == f"{TENANT_ID}/job/old" and job.file_size_bytes == 123
    assert PERIODIC_TOO_LARGE_MESSAGE in job.error_message


def test_estimate_failure_does_not_advance_last_run_at():
    """InfluxDBの一時障害では次回ポーリングで再試行する(期間はlast_run_atから続くので隙間なし)。"""
    last = datetime.now(timezone.utc) - timedelta(days=2)
    schedule = SimpleNamespace(tenant_id=TENANT_ID, frequency="daily", device_ids=None,
                               group_id=None, enabled=True, last_run_at=last)
    job = ExportJob(tenant_id=TENANT_ID, kind="periodic")
    db = _db([schedule], job=job)
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.estimate_export_row_count",
               side_effect=InfluxUnavailableError("down")), \
         patch("app.services.export_scheduler.run_export_job") as mock_run:
        run_due_periodic_exports()
    mock_run.assert_not_called()
    assert schedule.last_run_at == last
    assert job.status == "failed"


def test_group_scoped_schedule_estimates_only_group_devices():
    schedule = SimpleNamespace(tenant_id=TENANT_ID, frequency="daily", device_ids=None,
                               group_id="33333333-3333-3333-3333-333333333333", enabled=True,
                               last_run_at=None)
    job = ExportJob(tenant_id=TENANT_ID, kind="periodic")
    db = _db([schedule], job=job)
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_jobs.list_group_device_ids", return_value=["dev-a"]), \
         patch("app.services.export_scheduler.estimate_export_row_count", return_value=1) as mock_est, \
         patch("app.services.export_scheduler.run_export_job"):
        run_due_periodic_exports()
    assert mock_est.call_args.args[3] == ["dev-a"]
