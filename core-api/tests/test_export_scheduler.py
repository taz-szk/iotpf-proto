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
