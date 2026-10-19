from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from app.services.export_scheduler import run_due_periodic_exports


def _schedule(frequency, last_run_at):
    s = MagicMock(tenant_id="11111111-1111-1111-1111-111111111111", frequency=frequency,
                  device_ids=None, group_id=None, enabled=True, last_run_at=last_run_at)
    return s


def test_run_due_periodic_exports_skips_not_yet_due():
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [_schedule("daily", recent)]
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.run_export_job") as mock_run:
        db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
        run_due_periodic_exports()
    mock_run.assert_not_called()


def test_run_due_periodic_exports_runs_when_due():
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db = MagicMock()
    db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
    db.query.return_value.filter.return_value.all.return_value = [_schedule("daily", old)]
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.run_export_job") as mock_run:
        run_due_periodic_exports()
    mock_run.assert_called_once()


def test_run_due_periodic_exports_does_not_use_adhoc_lock_or_size_gate():
    """回帰テスト: 将来誰かが「DRYだから」とrequest_adhoc_exportやそのサイズ推定関数を
    呼ぶように変更しても、このテストが失敗して検知できるようにする。想定される呼び方を
    3パターンとも網羅する:
    - app.services.export_scheduler.request_adhoc_export /
      app.services.export_scheduler.estimate_export_row_count:
      scheduler内で`from app.services.export_jobs import request_adhoc_export`や
      `from app.services.export_csv import estimate_export_row_count`のようにbare-name
      importしてローカル名として呼ぶパターンを検知(現状は存在しない名前なのでcreate=True
      で仮に生やす)。
    - app.services.export_jobs.request_adhoc_export / estimate_export_row_count:
      `export_jobs.request_adhoc_export(...)`のようにモジュール参照で呼ぶパターンを検知。
    - app.services.export_csv.estimate_export_row_count: サイズ推定を
      `export_csv.estimate_export_row_count(...)`のようにモジュール参照で直接呼ぶパターンを
      検知。
    """
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db = MagicMock()
    db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
    db.query.return_value.filter.return_value.all.return_value = [_schedule("daily", old)]
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.run_export_job"), \
         patch("app.services.export_scheduler.request_adhoc_export", create=True) as mock_adhoc_local, \
         patch("app.services.export_scheduler.estimate_export_row_count", create=True) as mock_estimate_local, \
         patch("app.services.export_jobs.request_adhoc_export") as mock_adhoc_source, \
         patch("app.services.export_jobs.estimate_export_row_count") as mock_estimate_via_jobs, \
         patch("app.services.export_csv.estimate_export_row_count") as mock_estimate_source:
        run_due_periodic_exports()
    mock_adhoc_local.assert_not_called()
    mock_estimate_local.assert_not_called()
    mock_adhoc_source.assert_not_called()
    mock_estimate_via_jobs.assert_not_called()
    mock_estimate_source.assert_not_called()


def test_run_due_periodic_exports_calls_run_export_job_synchronously():
    """回帰テスト: run_export_jobが新規スレッドを立てず同期呼び出しされることを確認する。
    将来threading.Thread(target=run_export_job, ...).start()のようにラップされた場合に
    threading.Threadが呼ばれたことで検知できるようにする。"""
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db = MagicMock()
    db.__enter__ = lambda s: db; db.__exit__ = MagicMock(return_value=False)
    db.query.return_value.filter.return_value.all.return_value = [_schedule("daily", old)]
    with patch("app.services.export_scheduler.SessionLocal", return_value=db), \
         patch("app.services.export_scheduler.run_export_job") as mock_run, \
         patch("threading.Thread") as mock_thread:
        run_due_periodic_exports()
    mock_run.assert_called_once()
    mock_thread.assert_not_called()
