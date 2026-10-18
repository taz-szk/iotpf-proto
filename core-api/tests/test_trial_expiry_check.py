from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from app.scripts.trial_expiry_check import run_trial_expiry_check


def _tenant(id_, status_, trial_expires_at, warning_sent=None, ext_requested=None):
    return MagicMock(
        id=id_, name=f"tenant-{id_}", status=status_, signup_source="self_signup",
        trial_expires_at=trial_expires_at, trial_warning_sent_at=warning_sent,
        trial_extension_requested_at=ext_requested,
        influxdb_org_id="org", grafana_org_id="5",
    )


def _session_ctx(tenants):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.all.return_value = tenants
    mock_db.execute.return_value.scalar.return_value = "admin@example.com"
    return mock_db


def test_sends_warning_for_tenant_expiring_within_7_days():
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now + timedelta(days=5))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.send_trial_expiry_warning_email") as mock_mail, \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        result = run_trial_expiry_check()
    assert mock_mail.called
    assert tenant.trial_warning_sent_at is not None
    mock_teardown.assert_not_called()
    assert result == {"warned": 1, "deleted": 0}


def test_does_not_rewarn_already_warned_tenant():
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now + timedelta(days=5), warning_sent=now - timedelta(days=1))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.send_trial_expiry_warning_email") as mock_mail, \
         patch("app.scripts.trial_expiry_check.teardown_tenant"):
        mock_session.return_value = _session_ctx([tenant])
        run_trial_expiry_check()
    mock_mail.assert_not_called()


def test_deletes_expired_tenant_already_warned_without_extension_request():
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now - timedelta(days=1), warning_sent=now - timedelta(days=8))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.send_trial_expiry_warning_email"), \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        result = run_trial_expiry_check()
    assert tenant.status == "deleted"
    mock_teardown.assert_called_once_with("t1", "org", "5")
    assert result == {"warned": 0, "deleted": 1}


def test_does_not_delete_when_extension_requested_recently():
    now = datetime.now(timezone.utc)
    tenant = _tenant(
        "t1", "active", now - timedelta(days=1),
        warning_sent=now - timedelta(days=8), ext_requested=now - timedelta(days=1),
    )
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        run_trial_expiry_check()
    mock_teardown.assert_not_called()
    assert tenant.status == "active"


def test_deletes_when_extension_request_is_stale():
    # 延長申請が7日より前の古いもので無視された(放置された)場合は、次の削除サイクルで削除する
    now = datetime.now(timezone.utc)
    tenant = _tenant(
        "t1", "active", now - timedelta(days=1),
        warning_sent=now - timedelta(days=8), ext_requested=now - timedelta(days=10),
    )
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        result = run_trial_expiry_check()
    assert tenant.status == "deleted"
    mock_teardown.assert_called_once_with("t1", "org", "5")
    assert result == {"warned": 0, "deleted": 1}


def test_expired_tenant_never_warned_gets_warning_instead_of_deletion():
    # cronが止まっていた等でトライアル期限前に警告が送られなかった場合、
    # いきなり削除せず、まず警告を送って次サイクル以降の削除に回す
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now - timedelta(days=1))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.send_trial_expiry_warning_email", return_value=True) as mock_mail, \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        result = run_trial_expiry_check()
    assert mock_mail.called
    assert tenant.trial_warning_sent_at is not None
    mock_teardown.assert_not_called()
    assert tenant.status == "active"
    assert result == {"warned": 1, "deleted": 0}


def test_warning_flag_not_set_when_send_fails():
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now + timedelta(days=5))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.send_trial_expiry_warning_email", return_value=False), \
         patch("app.scripts.trial_expiry_check.teardown_tenant"):
        mock_session.return_value = _session_ctx([tenant])
        result = run_trial_expiry_check()
    assert tenant.trial_warning_sent_at is None
    assert result == {"warned": 0, "deleted": 0}


def test_cleans_up_expired_pending_signup_requests():
    now = datetime.now(timezone.utc)
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session:
        db = _session_ctx([])
        mock_session.return_value = db
        run_trial_expiry_check()
    db.query.return_value.filter.return_value.delete.assert_called_once()


def test_does_not_delete_suspended_tenant():
    # status=="active"限定のクエリ条件自体が守るべき不変条件。ここではクエリが
    # 返したテナント一覧自体にsuspendedが含まれないことを前提に、仮にactive以外が
    # 混入しても削除対象のフィルタ条件(status確認)で弾かれることを確認する。
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "suspended", now - timedelta(days=1))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        run_trial_expiry_check()
    mock_teardown.assert_not_called()
