"""通知の配信結果(成功/失敗)を返し、ルールごとに直近の結果として記録することのテスト。
結果にはWebhook URLも宛先メールも含めない(画面・APIに出るため)。"""
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app import notifier
from app.notifier import notify, send_alert_email, send_alert_slack

SECRET = "abcdEFGHijklMNOPqrstUVWX"
URL = "https://hooks.slack.com/" + "services/T01234567/B01234567/" + SECRET
KW = dict(tenant_id="tenant-001", device_id="device-001", sensor_key="temperature",
          condition="above", threshold=80.0, current_value=85.3, severity="critical")


def _resp(status, text="ok"):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


# ---------- Slack の結果 ----------

def test_slack_success_returns_ok():
    with patch("app.notifier.httpx.post", return_value=_resp(200)):
        assert send_alert_slack(URL, **KW) == {"ok": True, "error": None}


@pytest.mark.parametrize("status,body,expected", [
    (404, "no_service", "404"),
    (403, "invalid_token", "403"),
    (410, "channel_is_archived", "410"),
    (400, "invalid_payload", "400"),
    (500, "oops", "500"),
])
def test_slack_http_failures_are_described_without_the_url(status, body, expected):
    with patch("app.notifier.httpx.post", return_value=_resp(status, body)):
        r = send_alert_slack(URL, **KW)
    assert r["ok"] is False
    assert expected in r["error"]
    assert SECRET not in json.dumps(r)


def test_slack_no_service_and_archived_have_actionable_hints():
    with patch("app.notifier.httpx.post", return_value=_resp(404, "no_service")):
        assert "Webhook" in send_alert_slack(URL, **KW)["error"]
    with patch("app.notifier.httpx.post", return_value=_resp(410, "channel_is_archived")):
        assert "アーカイブ" in send_alert_slack(URL, **KW)["error"]


def test_slack_network_errors_are_classified_and_never_leak_the_url():
    for exc, hint in ((httpx.ConnectError(f"failed {URL}"), "接続"), (httpx.ReadTimeout(f"timeout {URL}"), "タイムアウト"),
                      (RuntimeError(f"weird {URL}"), "RuntimeError")):
        with patch("app.notifier.httpx.post", side_effect=exc):
            r = send_alert_slack(URL, **KW)
        assert r["ok"] is False and hint in r["error"]
        assert SECRET not in json.dumps(r)


def test_slack_invalid_url_is_a_failure_result_not_a_silent_skip():
    with patch("app.notifier.httpx.post") as post:
        r = send_alert_slack("https://evil.example/x", **KW)
    post.assert_not_called()
    assert r["ok"] is False and "形式" in r["error"]


# ---------- メールの結果 ----------

def test_email_success_and_failure_results():
    with patch("app.notifier.smtplib.SMTP") as smtp:
        smtp.return_value.__enter__ = lambda s: MagicMock()
        smtp.return_value.__exit__ = MagicMock(return_value=False)
        assert send_alert_email(to_emails=["ops@example.com"], **KW) == {"ok": True, "error": None}

    with patch("app.notifier.smtplib.SMTP", side_effect=ConnectionRefusedError("ops@example.com refused")):
        r = send_alert_email(to_emails=["ops@example.com"], **KW)
    assert r["ok"] is False and "ConnectionRefusedError" in r["error"]
    assert "ops@example.com" not in json.dumps(r)


def test_email_with_no_recipients_is_not_attempted():
    with patch("app.notifier.smtplib.SMTP") as smtp:
        assert send_alert_email(to_emails=[], **KW) is None
    smtp.assert_not_called()


# ---------- notify がチャンネルごとの結果を返す ----------

def test_notify_returns_a_result_per_attempted_channel():
    with patch("app.notifier.send_alert_email", return_value={"ok": True, "error": None}), \
         patch("app.notifier.send_alert_slack", return_value={"ok": False, "error": "HTTP 404"}):
        res = notify({"notify_emails": ["a@example.com"], "slack_webhook_url": URL}, **KW)
    assert res == {"email": {"ok": True, "error": None}, "slack": {"ok": False, "error": "HTTP 404"}}


def test_notify_marks_channels_that_were_not_configured_as_none():
    with patch("app.notifier.send_alert_email") as email, patch("app.notifier.send_alert_slack") as slack:
        res = notify({"notify_emails": [], "slack_webhook_url": None}, **KW)
    assert res == {"email": None, "slack": None}
    email.assert_not_called()
    slack.assert_not_called()


def test_notify_turns_an_unexpected_exception_into_a_failure_result():
    with patch("app.notifier.send_alert_email", side_effect=RuntimeError("boom a@example.com")), \
         patch("app.notifier.send_alert_slack", return_value={"ok": True, "error": None}):
        res = notify({"notify_emails": ["a@example.com"], "slack_webhook_url": URL}, **KW)
    assert res["email"]["ok"] is False and "RuntimeError" in res["email"]["error"]
    assert "a@example.com" not in json.dumps(res)
    assert res["slack"] == {"ok": True, "error": None}


# ---------- DB への記録 ----------

def _conn():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def test_record_merges_only_attempted_channels_with_a_timestamp():
    from app.db import record_notify_status
    conn, cur = _conn()
    with patch("app.db.get_conn", return_value=conn):
        record_notify_status("11111111-1111-1111-1111-111111111111", "rule-1",
                             {"email": None, "slack": {"ok": False, "error": "HTTP 404"}})
    sql, params = cur.execute.call_args.args
    assert "notify_status" in sql and "||" in sql and "tenant_11111111_1111_1111_1111_111111111111" in sql
    stored = json.loads(params[0])
    assert list(stored) == ["slack"]
    assert stored["slack"]["ok"] is False and stored["slack"]["error"] == "HTTP 404" and stored["slack"]["at"]
    assert params[1] == "rule-1"
    conn.commit.assert_called_once()


def test_record_does_nothing_when_no_channel_was_attempted():
    from app.db import record_notify_status
    with patch("app.db.get_conn") as gc:
        record_notify_status("11111111-1111-1111-1111-111111111111", "rule-1", {"email": None, "slack": None})
    gc.assert_not_called()


# ---------- scheduler が通知のたびに結果を記録する ----------

def test_scheduler_records_the_result_and_survives_a_recording_failure():
    from contextlib import ExitStack
    from app import scheduler

    rule = {"id": "r1", "device_id": "device-001", "group_id": None, "sensor_key": "temperature",
            "condition": "above", "threshold": 80, "severity": "warning",
            "notify_emails": [], "slack_webhook_url": URL}
    results = {"email": None, "slack": {"ok": True, "error": None}}
    for record_side_effect in (None, RuntimeError("db down")):
        with ExitStack() as stack:
            stack.enter_context(patch("app.scheduler.get_active_alert_rules", return_value=[rule]))
            stack.enter_context(patch("app.scheduler._make_group_device_resolver", return_value=lambda g: []))
            stack.enter_context(patch("app.scheduler.evaluate_rule", return_value=(True, 90.0)))
            stack.enter_context(patch("app.scheduler.get_unresolved_event", return_value=None))
            stack.enter_context(patch("app.scheduler.create_alert_event", return_value="ev-1"))
            mark = stack.enter_context(patch("app.scheduler.mark_event_notified"))
            stack.enter_context(patch("app.scheduler.notify", return_value=results))
            rec = stack.enter_context(patch("app.scheduler.record_notify_status", side_effect=record_side_effect))
            scheduler._evaluate_tenant("tenant-001", "org", "tok")
        rec.assert_called_once_with("tenant-001", "r1", results)
        mark.assert_called_once()  # 記録の失敗で後続の処理を止めない


# ---------- Slack/SMTPの実際のエラーに、対処の分かる説明を付ける ----------

@pytest.mark.parametrize("body,hint", [
    ("no_team", "URL"),               # 実在しないWebhookに対するSlackの実際の応答
    ("no_service", "Webhook"),
    ("no_service_id", "Webhook"),
    ("channel_not_found", "チャンネル"),
    ("action_prohibited", "制限"),
])
def test_slack_error_bodies_have_actionable_hints(body, hint):
    with patch("app.notifier.httpx.post", return_value=_resp(404, body)):
        r = send_alert_slack(URL, **KW)
    assert "404" in r["error"] and hint in r["error"]


def test_slack_rate_limit_is_explained():
    with patch("app.notifier.httpx.post", return_value=_resp(429, "rate_limited")):
        assert "制限" in send_alert_slack(URL, **KW)["error"]


def test_email_errors_have_actionable_hints_and_keep_the_class_name():
    import smtplib
    import socket
    cases = [
        (socket.gaierror(-2, "Name or service not known"), "ホスト名", "gaierror"),
        (ConnectionRefusedError("refused"), "接続できません", "ConnectionRefusedError"),
        (TimeoutError("timed out"), "タイムアウト", "TimeoutError"),
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "認証", "SMTPAuthenticationError"),
        (smtplib.SMTPRecipientsRefused({"ops@example.com": (550, b"no")}), "宛先", "SMTPRecipientsRefused"),
    ]
    for exc, hint, cls in cases:
        with patch("app.notifier.smtplib.SMTP", side_effect=exc):
            r = send_alert_email(to_emails=["ops@example.com"], **KW)
        assert r["ok"] is False and hint in r["error"] and cls in r["error"]
        assert "ops@example.com" not in r["error"]
