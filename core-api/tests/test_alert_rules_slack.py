"""アラートルールのSlack Webhook URL(ルール単位の通知先)のAPIテスト。URLは秘密情報なので、
検証(hooks.slack.comのみ)とマスク(レスポンスに載せない)を確認する。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
RULE_ID = "33333333-3333-3333-3333-333333333333"
SECRET = "abcdEFGHijklMNOPqrstUVWX"
URL = "https://hooks.slack.com/" + "services/T01234567/B01234567/" + SECRET  # GitHubのpush protectionがダミーURLを本物と誤検出するため、連結して組み立てる
BASE = {"sensor_key": "temperature", "condition": "above", "threshold": 30}
NOTIFY_STATUS = {"slack": {"ok": False, "error": "HTTP 404", "at": "2026-09-24T05:12:00+00:00"}}


def _platform_headers():
    t = create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})
    return {"Authorization": f"Bearer {t}"}


def _tenant_cookies(role="operator"):
    t = create_access_token({"sub": "user-id", "email": "u@test.com", "type": "tenant",
                            "tenant_id": TENANT_ID, "role": role})
    return {"iot_token": t}


def _row(slack=URL, **kw):
    base = dict(id=RULE_ID, device_id=None, group_id=None, sensor_key="temperature", condition="above",
                threshold=30, trigger_mode="consecutive", consecutive_count=3, duration_sec=60,
                severity="warning", notify_emails=["a@example.com"], slack_webhook_url=slack, is_active=True,
                notify_status=NOTIFY_STATUS,
                last_triggered_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    base.update(kw)
    return SimpleNamespace(**base)


def _insert_params(mock_db):
    return mock_db.execute.call_args_list[0].args[1]


# ---------- プラットフォーム側 ----------

def test_platform_create_with_slack_url_stores_it_but_never_echoes_it():
    with patch("app.routers.alert_rules.SessionLocal") as sl:
        db = sl.return_value.__enter__.return_value
        resp = client.post(f"/tenants/{TENANT_ID}/alert-rules", headers=_platform_headers(),
                           json={**BASE, "slack_webhook_url": URL})
    assert resp.status_code == 201
    assert _insert_params(db)["slack"] == URL
    body = resp.json()
    assert body["slack_configured"] is True and body["slack_webhook_hint"] == "…UVWX"
    assert SECRET not in resp.text and "slack_webhook_url" not in body


def test_platform_create_without_slack_url():
    with patch("app.routers.alert_rules.SessionLocal") as sl:
        db = sl.return_value.__enter__.return_value
        resp = client.post(f"/tenants/{TENANT_ID}/alert-rules", headers=_platform_headers(), json=BASE)
    assert resp.status_code == 201
    assert _insert_params(db)["slack"] is None
    assert resp.json()["slack_configured"] is False


def test_platform_create_rejects_non_slack_url():
    resp = client.post(f"/tenants/{TENANT_ID}/alert-rules", headers=_platform_headers(),
                       json={**BASE, "slack_webhook_url": "http://169.254.169.254/latest/meta-data"})
    assert resp.status_code == 422


def test_platform_list_masks_the_url():
    with patch("app.routers.alert_rules.SessionLocal") as sl:
        sl.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [_row()]
        resp = client.get(f"/tenants/{TENANT_ID}/alert-rules", headers=_platform_headers())
    assert resp.status_code == 200
    assert SECRET not in resp.text
    assert resp.json()[0]["slack_configured"] is True


def _patch_platform(row_before, row_after):
    from unittest.mock import MagicMock
    sl = patch("app.routers.alert_rules.SessionLocal")
    mock_sl = sl.start()
    db = mock_sl.return_value.__enter__.return_value
    db.execute.return_value.fetchone.side_effect = [row_before, row_after]
    return sl, db


def test_platform_update_sets_slack_url():
    sl, db = _patch_platform(_row(slack=None), _row())
    try:
        resp = client.patch(f"/tenants/{TENANT_ID}/alert-rules/{RULE_ID}", headers=_platform_headers(),
                            json={"slack_webhook_url": URL})
    finally:
        sl.stop()
    assert resp.status_code == 200
    update_call = db.execute.call_args_list[1]
    assert "slack_webhook_url = :slack_webhook_url" in str(update_call.args[0])
    assert update_call.args[1]["slack_webhook_url"] == URL
    assert SECRET not in resp.text


def test_platform_update_null_clears_and_absence_keeps():
    sl, db = _patch_platform(_row(), _row(slack=None))
    try:
        resp = client.patch(f"/tenants/{TENANT_ID}/alert-rules/{RULE_ID}", headers=_platform_headers(),
                            json={"slack_webhook_url": None})
    finally:
        sl.stop()
    assert resp.status_code == 200
    assert db.execute.call_args_list[1].args[1]["slack_webhook_url"] is None
    assert resp.json()["slack_configured"] is False

    sl, db = _patch_platform(_row(), _row())
    try:
        client.patch(f"/tenants/{TENANT_ID}/alert-rules/{RULE_ID}", headers=_platform_headers(),
                     json={"severity": "critical"})
    finally:
        sl.stop()
    assert "slack_webhook_url" not in str(db.execute.call_args_list[1].args[0])


def test_platform_update_rejects_non_slack_url():
    resp = client.patch(f"/tenants/{TENANT_ID}/alert-rules/{RULE_ID}", headers=_platform_headers(),
                        json={"slack_webhook_url": "https://evil.example/x"})
    assert resp.status_code == 422


# ---------- テナント側 ----------

def test_tenant_create_stores_url_and_does_not_echo_it():
    with patch("app.routers.tenant_portal.SessionLocal") as sl:
        db = sl.return_value.__enter__.return_value
        resp = client.post("/tenant-portal/me/alert-rules", cookies=_tenant_cookies(),
                           json={**BASE, "slack_webhook_url": URL})
    assert resp.status_code == 201
    assert _insert_params(db)["slack"] == URL
    assert SECRET not in resp.text
    assert resp.json()["slack_configured"] is True


def test_tenant_create_rejects_non_slack_url():
    resp = client.post("/tenant-portal/me/alert-rules", cookies=_tenant_cookies(),
                       json={**BASE, "slack_webhook_url": "https://hooks.slack.com.evil.example/services/T0/B0/x"})
    assert resp.status_code == 422


def test_tenant_list_masks_the_url():
    with patch("app.routers.tenant_portal.SessionLocal") as sl:
        sl.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [_row()]
        resp = client.get("/tenant-portal/me/alert-rules", cookies=_tenant_cookies("viewer"))
    assert resp.status_code == 200
    assert SECRET not in resp.text
    assert resp.json()[0]["slack_configured"] is True and resp.json()[0]["slack_webhook_hint"] == "…UVWX"


def test_tenant_update_sets_clears_and_masks():
    with patch("app.routers.tenant_portal.SessionLocal") as sl:
        db = sl.return_value.__enter__.return_value
        db.execute.return_value.fetchone.side_effect = [_row(slack=None), _row()]
        resp = client.patch(f"/tenant-portal/me/alert-rules/{RULE_ID}", cookies=_tenant_cookies(),
                            json={"slack_webhook_url": URL})
    assert resp.status_code == 200
    assert db.execute.call_args_list[1].args[1]["slack_webhook_url"] == URL
    assert SECRET not in resp.text

    with patch("app.routers.tenant_portal.SessionLocal") as sl:
        db = sl.return_value.__enter__.return_value
        db.execute.return_value.fetchone.side_effect = [_row(), _row(slack=None)]
        resp = client.patch(f"/tenant-portal/me/alert-rules/{RULE_ID}", cookies=_tenant_cookies(),
                            json={"slack_webhook_url": None})
    assert resp.status_code == 200
    assert db.execute.call_args_list[1].args[1]["slack_webhook_url"] is None
    assert resp.json()["slack_configured"] is False


# ---------- 配信結果(notify_status)の表示 ----------

def test_platform_list_includes_the_latest_delivery_status():
    with patch("app.routers.alert_rules.SessionLocal") as sl:
        sl.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [_row()]
        resp = client.get(f"/tenants/{TENANT_ID}/alert-rules", headers=_platform_headers())
    assert resp.json()[0]["notify_status"] == NOTIFY_STATUS


def test_tenant_list_and_update_include_the_latest_delivery_status():
    with patch("app.routers.tenant_portal.SessionLocal") as sl:
        sl.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [_row()]
        resp = client.get("/tenant-portal/me/alert-rules", cookies=_tenant_cookies("viewer"))
    assert resp.json()[0]["notify_status"] == NOTIFY_STATUS

    with patch("app.routers.tenant_portal.SessionLocal") as sl:
        db = sl.return_value.__enter__.return_value
        db.execute.return_value.fetchone.side_effect = [_row(), _row()]
        resp = client.patch(f"/tenant-portal/me/alert-rules/{RULE_ID}", cookies=_tenant_cookies(),
                            json={"severity": "critical"})
    assert resp.json()["notify_status"] == NOTIFY_STATUS


def test_rule_without_any_delivery_yet_has_null_status():
    with patch("app.routers.alert_rules.SessionLocal") as sl:
        sl.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [_row()]
        sl.return_value.__enter__.return_value.execute.return_value.fetchall.return_value[0].notify_status = None
        resp = client.get(f"/tenants/{TENANT_ID}/alert-rules", headers=_platform_headers())
    assert resp.json()[0]["notify_status"] is None
