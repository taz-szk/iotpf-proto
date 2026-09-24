"""アラート通知の「テスト送信」(Slack / メール)のテスト。
入力の検証、Slack/SMTPの結果の日本語化、レート制限、権限、監査ログ、URLを返さないことを確認する。"""
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import alert_test_notify, slack_webhook
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
RULE_ID = "33333333-3333-3333-3333-333333333333"
SECRET = "abcdEFGHijklMNOPqrstUVWX"
URL = "https://hooks.slack.com/" + "services/T01234567/B01234567/" + SECRET
PLATFORM_PATH = f"/tenants/{TENANT_ID}/alert-rules/test-notification"
TENANT_PATH = "/tenant-portal/me/alert-rules/test-notification"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    alert_test_notify.reset_rate_limits()
    monkeypatch.setattr(alert_test_notify, "get_tenant_name", lambda tenant_id: "Acme <Corp>")
    audit = MagicMock()
    monkeypatch.setattr(alert_test_notify, "log_audit", audit)
    yield audit
    alert_test_notify.reset_rate_limits()


def _platform():
    t = create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})
    return {"headers": {"Authorization": f"Bearer {t}"}}


def _tenant(role="operator", sub="user-id"):
    t = create_access_token({"sub": sub, "email": "u@test.com", "type": "tenant",
                            "tenant_id": TENANT_ID, "role": role})
    return {"cookies": {"iot_token": t}}


def _ok_resp():
    r = MagicMock(); r.status_code = 200; r.text = "ok"
    return r


def _fail_resp(status, body):
    r = MagicMock(); r.status_code = status; r.text = body
    return r


# ---------- slack_webhook.post_slack_message ----------

def test_post_slack_message_ok_and_payload():
    with patch("app.services.slack_webhook.httpx.post", return_value=_ok_resp()) as post:
        assert slack_webhook.post_slack_message(URL, "hello") == {"ok": True, "error": None}
    assert post.call_args.args[0] == URL and post.call_args.kwargs["json"] == {"text": "hello"}
    assert post.call_args.kwargs["timeout"] == 10 and not post.call_args.kwargs.get("follow_redirects")


@pytest.mark.parametrize("status,body,hint", [
    (404, "no_service", "Webhook"), (403, "invalid_token", "無効"),
    (410, "channel_is_archived", "アーカイブ"), (500, "x", "500"),
])
def test_post_slack_message_failures_are_explained_without_the_url(status, body, hint):
    with patch("app.services.slack_webhook.httpx.post", return_value=_fail_resp(status, body)):
        r = slack_webhook.post_slack_message(URL, "hello")
    assert r["ok"] is False and hint in r["error"] and str(status) in r["error"]
    assert SECRET not in json.dumps(r)


def test_post_slack_message_network_errors_never_leak_the_url():
    for exc, hint in ((httpx.ConnectError(f"x {URL}"), "接続"), (httpx.ReadTimeout(f"x {URL}"), "タイムアウト")):
        with patch("app.services.slack_webhook.httpx.post", side_effect=exc):
            r = slack_webhook.post_slack_message(URL, "hello")
        assert r["ok"] is False and hint in r["error"] and SECRET not in json.dumps(r)


def test_post_slack_message_refuses_non_slack_urls():
    with patch("app.services.slack_webhook.httpx.post") as post:
        r = slack_webhook.post_slack_message("http://169.254.169.254/x", "hello")
    post.assert_not_called()
    assert r["ok"] is False


# ---------- mailer.send_test_email ----------

def test_send_test_email_result_and_no_address_leak():
    from app.services.mailer import send_test_email
    with patch("app.services.mailer.smtplib.SMTP") as smtp:
        smtp.return_value.__enter__ = lambda s: MagicMock()
        smtp.return_value.__exit__ = MagicMock(return_value=False)
        assert send_test_email(["ops@example.com"], "Acme", "admin@iot.local") == {"ok": True, "error": None}
    with patch("app.services.mailer.smtplib.SMTP", side_effect=ConnectionRefusedError("ops@example.com")):
        r = send_test_email(["ops@example.com"], "Acme", "admin@iot.local")
    assert r["ok"] is False and "ConnectionRefusedError" in r["error"] and "ops@example.com" not in r["error"]


def test_send_test_email_sanitizes_header_values():
    from app.services.mailer import send_test_email
    with patch("app.services.mailer.smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__ = lambda s: server
        smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_test_email(["ops@example.com"], "Acme\r\nBcc: evil@example.com", "admin@iot.local")
    raw = server.sendmail.call_args.args[2]
    assert "\r\nBcc:" not in raw.split("\r\n\r\n")[0] and "\nBcc:" not in raw.split("\n\n")[0]


# ---------- エンドポイント(共通) ----------

@pytest.mark.parametrize("path,auth", [(PLATFORM_PATH, _platform), (TENANT_PATH, _tenant)])
def test_slack_test_with_a_typed_url(path, auth, _isolate):
    with patch("app.services.alert_test_notify.post_slack_message", return_value={"ok": True, "error": None}) as post:
        resp = client.post(path, json={"channel": "slack", "slack_webhook_url": URL}, **auth())
    assert resp.status_code == 200 and resp.json() == {"ok": True, "error": None}
    assert post.call_args.args[0] == URL
    text = post.call_args.args[1]
    assert "TEST" in text and "Acme &lt;Corp&gt;" in text and "<Corp>" not in text  # エスケープ済み
    _isolate.assert_called_once()
    assert SECRET not in json.dumps(_isolate.call_args.args + tuple(_isolate.call_args.kwargs.values()), default=str)


@pytest.mark.parametrize("path,auth", [(PLATFORM_PATH, _platform), (TENANT_PATH, _tenant)])
def test_failed_delivery_is_a_200_with_ok_false_and_never_echoes_the_url(path, auth, _isolate):
    fail = {"ok": False, "error": "HTTP 404（Webhookが存在しない、または削除されています）"}
    with patch("app.services.alert_test_notify.post_slack_message", return_value=fail):
        resp = client.post(path, json={"channel": "slack", "slack_webhook_url": URL}, **auth())
    assert resp.status_code == 200 and resp.json() == {"ok": False, "error": fail["error"]}
    assert SECRET not in resp.text
    assert _isolate.call_args.kwargs.get("result") == "failure"


@pytest.mark.parametrize("path,auth", [(PLATFORM_PATH, _platform), (TENANT_PATH, _tenant)])
@pytest.mark.parametrize("body", [
    {"channel": "slack"},                                                          # URLもrule_idもない
    {"channel": "slack", "slack_webhook_url": URL, "rule_id": RULE_ID},            # 両方
    {"channel": "slack", "slack_webhook_url": "https://evil.example/x"},           # Slack以外
    {"channel": "slack", "rule_id": "not-a-uuid"},
    {"channel": "email"},
    {"channel": "email", "emails": []},
    {"channel": "email", "emails": ["not-an-email"]},
    {"channel": "email", "emails": [f"u{i}@example.com" for i in range(6)]},
    {"channel": "sms"},
])
def test_invalid_requests_are_rejected_with_422(path, auth, body):
    with patch("app.services.alert_test_notify.post_slack_message") as post, \
         patch("app.services.alert_test_notify.send_test_email") as mail:
        resp = client.post(path, json=body, **auth())
    assert resp.status_code == 422
    post.assert_not_called()
    mail.assert_not_called()


@pytest.mark.parametrize("path,auth", [(PLATFORM_PATH, _platform), (TENANT_PATH, _tenant)])
def test_slack_test_with_a_saved_rule_uses_the_stored_url(path, auth):
    with patch("app.services.alert_test_notify.get_rule_slack_url", return_value=URL) as lookup, \
         patch("app.services.alert_test_notify.post_slack_message", return_value={"ok": True, "error": None}) as post:
        resp = client.post(path, json={"channel": "slack", "rule_id": RULE_ID}, **auth())
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert post.call_args.args[0] == URL
    assert lookup.call_args.args[1] == RULE_ID
    assert lookup.call_args.args[0] == f"tenant_{TENANT_ID.replace('-', '_')}"


def test_saved_rule_missing_or_without_url():
    from fastapi import HTTPException
    with patch("app.services.alert_test_notify.get_rule_slack_url", side_effect=HTTPException(404, "Rule not found")):
        assert client.post(TENANT_PATH, json={"channel": "slack", "rule_id": RULE_ID}, **_tenant()).status_code == 404
    with patch("app.services.alert_test_notify.get_rule_slack_url", return_value=None):
        assert client.post(TENANT_PATH, json={"channel": "slack", "rule_id": RULE_ID}, **_tenant()).status_code == 400


@pytest.mark.parametrize("path,auth", [(PLATFORM_PATH, _platform), (TENANT_PATH, _tenant)])
def test_email_test(path, auth, _isolate):
    with patch("app.services.alert_test_notify.send_test_email", return_value={"ok": True, "error": None}) as mail:
        resp = client.post(path, json={"channel": "email", "emails": ["ops@example.com", "b@example.com"]}, **auth())
    assert resp.status_code == 200 and resp.json() == {"ok": True, "error": None}
    assert mail.call_args.args[0] == ["ops@example.com", "b@example.com"]
    assert "ops@example.com" not in json.dumps(_isolate.call_args.args + tuple(_isolate.call_args.kwargs.values()), default=str)


def test_rate_limit_is_5_per_minute_per_user_and_independent_between_users():
    body = {"channel": "slack", "slack_webhook_url": URL}
    with patch("app.services.alert_test_notify.post_slack_message", return_value={"ok": True, "error": None}) as post:
        codes = [client.post(TENANT_PATH, json=body, **_tenant()).status_code for _ in range(6)]
        other_user = client.post(TENANT_PATH, json=body, **_tenant(sub="someone-else")).status_code
    assert codes == [200] * 5 + [429]
    assert other_user == 200
    assert post.call_count == 6  # 429 のリクエストでは送信しない(5 + 別ユーザー1)


def test_permissions():
    body = {"channel": "slack", "slack_webhook_url": URL}
    assert client.post(TENANT_PATH, json=body, **_tenant("viewer")).status_code == 403
    assert client.post(TENANT_PATH, json=body).status_code == 401
    assert client.post(PLATFORM_PATH, json=body).status_code in (401, 403)
    # テナントのJWTでプラットフォーム側は使えない
    t = create_access_token({"sub": "u", "email": "u@test.com", "type": "tenant", "tenant_id": TENANT_ID, "role": "admin"})
    assert client.post(PLATFORM_PATH, json=body, headers={"Authorization": f"Bearer {t}"}).status_code == 401


# ---------- Slack/SMTPの実際のエラーに、対処の分かる説明を付ける ----------

@pytest.mark.parametrize("body,hint", [
    ("no_team", "URL"), ("no_service", "Webhook"), ("no_service_id", "Webhook"),
    ("channel_not_found", "チャンネル"), ("action_prohibited", "制限"),
])
def test_slack_error_bodies_have_actionable_hints(body, hint):
    with patch("app.services.slack_webhook.httpx.post", return_value=_fail_resp(404, body)):
        r = slack_webhook.post_slack_message(URL, "hello")
    assert "404" in r["error"] and hint in r["error"]


def test_slack_rate_limit_is_explained():
    with patch("app.services.slack_webhook.httpx.post", return_value=_fail_resp(429, "rate_limited")):
        assert "制限" in slack_webhook.post_slack_message(URL, "hello")["error"]


def test_smtp_errors_have_actionable_hints_and_keep_the_class_name():
    import smtplib
    import socket
    from app.services.mailer import send_test_email
    cases = [
        (socket.gaierror(-2, "Name or service not known"), "ホスト名", "gaierror"),
        (ConnectionRefusedError("refused"), "接続できません", "ConnectionRefusedError"),
        (TimeoutError("timed out"), "タイムアウト", "TimeoutError"),
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "認証", "SMTPAuthenticationError"),
        (smtplib.SMTPRecipientsRefused({"ops@example.com": (550, b"no")}), "宛先", "SMTPRecipientsRefused"),
    ]
    for exc, hint, cls in cases:
        with patch("app.services.mailer.smtplib.SMTP", side_effect=exc):
            r = send_test_email(["ops@example.com"], "Acme", "admin@iot.local")
        assert r["ok"] is False and hint in r["error"] and cls in r["error"]
        assert "ops@example.com" not in r["error"]
