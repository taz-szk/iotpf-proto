"""Slack通知が届かなかったとき、テナント管理者・プラットフォーム管理者へメールで知らせる。
メール(SES)のバウンス率に影響しないよう、送るのは「失敗に変わったとき」の1回だけ・届かない宛先は除外・件数に上限。"""
import email.header
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.failure_notice import MAX_RECIPIENTS, decide_failed_channels, filter_deliverable

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
FAIL = {"ok": False, "error": "HTTP 404（Webhook URLが正しくありません）"}
OK = {"ok": True, "error": None}


# ---------- 宛先の絞り込み ----------

def test_filter_drops_invalid_and_reserved_domains_and_dedupes():
    emails = [
        "Admin@Corp.co.jp", "admin@corp.co.jp",            # 大文字小文字違いの重複
        "ops@example.com", "x@example.net", "y@foo.example.org",   # 予約ドメイン(RFC 2606)
        "a@b.test", "a@b.invalid", "a@b.local", "a@localhost", "a@corp.localdomain",
        "not-an-email", "a@b", "", None, "sp ace@corp.co.jp", "two@@corp.co.jp",
        "real@corp.co.jp",
    ]
    assert filter_deliverable(emails) == ["admin@corp.co.jp", "real@corp.co.jp"]


def test_filter_keeps_order_and_caps_the_count():
    emails = [f"user{i}@corp.co.jp" for i in range(MAX_RECIPIENTS + 5)]
    out = filter_deliverable(emails)
    assert out == emails[:MAX_RECIPIENTS] and MAX_RECIPIENTS == 10


# ---------- 通知するかどうかの判定 ----------

def test_notifies_when_slack_starts_failing():
    assert decide_failed_channels(None, {"email": None, "slack": FAIL}, NOW) == ["slack"]
    assert decide_failed_channels({}, {"email": None, "slack": FAIL}, NOW) == ["slack"]
    assert decide_failed_channels({"slack": {"ok": True}}, {"slack": FAIL}, NOW) == ["slack"]


def test_does_not_notify_while_the_failure_continues():
    assert decide_failed_channels({"slack": {"ok": False, "error": "x"}}, {"slack": FAIL}, NOW) == []


def test_does_not_notify_on_success_or_when_slack_was_not_attempted():
    assert decide_failed_channels(None, {"slack": OK}, NOW) == []
    assert decide_failed_channels(None, {"email": OK, "slack": None}, NOW) == []
    assert decide_failed_channels(None, None, NOW) == []
    assert decide_failed_channels(None, MagicMock(), NOW) == []   # 想定外の値でも落ちない


def test_email_failures_never_trigger_admin_mail():
    """メールが失敗している状況で、さらにメールを重ねない(バウンス率・ループの回避)"""
    assert decide_failed_channels(None, {"email": FAIL, "slack": None}, NOW) == []
    assert decide_failed_channels(None, {"email": FAIL, "slack": FAIL}, NOW) == ["slack"]


def test_flapping_is_limited_by_a_one_hour_cooldown():
    recent = (NOW - timedelta(minutes=30)).isoformat()
    old = (NOW - timedelta(minutes=61)).isoformat()
    prev_recent = {"slack": {"ok": True}, "admin_notice": {"at": recent}}
    prev_old = {"slack": {"ok": True}, "admin_notice": {"at": old}}
    assert decide_failed_channels(prev_recent, {"slack": FAIL}, NOW) == []
    assert decide_failed_channels(prev_old, {"slack": FAIL}, NOW) == ["slack"]
    assert decide_failed_channels({"slack": {"ok": True}, "admin_notice": {"at": "garbage"}}, {"slack": FAIL}, NOW) == ["slack"]


# ---------- 失敗通知メール ----------

ALERT = dict(sensor_key="temperature", device_id="device-001", condition="above", threshold=80.0,
             current_value=85.3, severity="critical", resolved=False)


def test_failure_email_contents():
    from app.notifier import send_delivery_failure_email
    with patch("app.notifier.smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__ = lambda s: server
        smtp.return_value.__exit__ = MagicMock(return_value=False)
        r = send_delivery_failure_email(["admin@corp.co.jp", "root@platform.co.jp"], "Acme", "slack", FAIL["error"], ALERT)
    assert r == {"ok": True, "error": None}
    from_addr, to_addrs, raw = server.sendmail.call_args.args
    assert to_addrs == ["admin@corp.co.jp", "root@platform.co.jp"]
    import email
    msg = email.message_from_string(raw)
    body = msg.get_payload(0).get_payload(decode=True).decode("utf-8")
    subject = str(email.header.make_header(email.header.decode_header(msg["Subject"])))
    assert "Slack" in subject and "temperature" in subject
    for part in ("Acme", "HTTP 404", "temperature", "device-001", "above", "85.3", "critical"):
        assert part in body
    assert "hooks.slack.com" not in raw   # Webhook URLは含めない


def test_failure_email_notes_when_the_failed_message_was_a_recovery_notice():
    from app.notifier import send_delivery_failure_email
    with patch("app.notifier.smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__ = lambda s: server
        smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_delivery_failure_email(["a@corp.co.jp"], "Acme", "slack", "HTTP 404", {**ALERT, "resolved": True})
    import email
    body = email.message_from_string(server.sendmail.call_args.args[2]).get_payload(0).get_payload(decode=True).decode("utf-8")
    assert "復旧" in body


def test_failure_email_sanitizes_header_values_and_handles_smtp_errors():
    from app.notifier import send_delivery_failure_email
    with patch("app.notifier.smtplib.SMTP") as smtp:
        server = MagicMock()
        smtp.return_value.__enter__ = lambda s: server
        smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_delivery_failure_email(["a@corp.co.jp"], "Acme\r\nBcc: evil@x.com", "slack", "HTTP 404",
                                    {**ALERT, "sensor_key": "t\r\nBcc: evil@x.com"})
    raw = server.sendmail.call_args.args[2]
    assert "\nBcc:" not in raw.split("\n\n")[0]

    with patch("app.notifier.smtplib.SMTP", side_effect=ConnectionRefusedError("a@corp.co.jp refused")):
        r = send_delivery_failure_email(["a@corp.co.jp"], "Acme", "slack", "HTTP 404", ALERT)
    assert r["ok"] is False and "a@corp.co.jp" not in json.dumps(r)


def test_failure_email_with_no_recipients_is_not_sent():
    from app.notifier import send_delivery_failure_email
    with patch("app.notifier.smtplib.SMTP") as smtp:
        assert send_delivery_failure_email([], "Acme", "slack", "HTTP 404", ALERT) is None
    smtp.assert_not_called()


# ---------- DB ----------

def _conn(rows_by_call=()):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.side_effect = list(rows_by_call) or None
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def test_get_tenant_admin_contacts_reads_active_tenant_admins_then_platform_admins():
    from app.db import get_tenant_admin_contacts
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = {"name": "Acme"}
    cur.fetchall.side_effect = [[{"email": "Tenant@Corp.co.jp"}], [{"email": "root@platform.co.jp"}, {"email": "TENANT@corp.co.jp"}]]
    conn.cursor.return_value.__enter__.return_value = cur
    with patch("app.db.get_conn", return_value=conn):
        c = get_tenant_admin_contacts("11111111-1111-1111-1111-111111111111")
    sqls = [call.args[0] for call in cur.execute.call_args_list]
    assert any("tenant_11111111_1111_1111_1111_111111111111" in s and "role = 'admin'" in s and "is_active" in s for s in sqls)
    assert any("platform_users" in s and "is_active" in s for s in sqls)
    assert c == {"tenant_name": "Acme", "emails": ["tenant@corp.co.jp", "root@platform.co.jp"]}


def test_record_admin_notice_merges_a_timestamp_without_touching_channel_results():
    from app.db import record_admin_notice
    conn, cur = _conn()
    with patch("app.db.get_conn", return_value=conn):
        record_admin_notice("11111111-1111-1111-1111-111111111111", "rule-1", NOW)
    sql, params = cur.execute.call_args.args
    assert "notify_status" in sql and "||" in sql
    assert json.loads(params[0]) == {"admin_notice": {"at": NOW.isoformat()}}
    assert params[1] == "rule-1"
    conn.commit.assert_called_once()


def test_active_rules_query_includes_the_previous_notify_status():
    from app.db import get_active_alert_rules
    conn, cur = _conn([[]])
    with patch("app.db.get_conn", return_value=conn):
        get_active_alert_rules("11111111-1111-1111-1111-111111111111")
    assert "notify_status" in cur.execute.call_args.args[0]


# ---------- scheduler の統合 ----------

URL = "https://hooks.slack.com/" + "services/T01234567/B01234567/abcdEFGHijklMNOPqrstUVWX"
KW = dict(tenant_id="tenant-001", device_id="device-001", sensor_key="temperature", condition="above",
          threshold=80.0, current_value=85.3, severity="critical")


def _rule(status=None):
    return {"id": "r1", "notify_emails": [], "slack_webhook_url": URL, "notify_status": status}


def _run(rule, results, contacts=None, contacts_error=None, **kw):
    from app import scheduler
    contacts = contacts or {"tenant_name": "Acme", "emails": ["admin@corp.co.jp", "ops@example.com", "root@platform.co.jp"]}
    with patch("app.scheduler.notify", return_value=results), \
         patch("app.scheduler.record_notify_status"), \
         patch("app.scheduler.get_tenant_admin_contacts", return_value=contacts, side_effect=contacts_error) as gc, \
         patch("app.scheduler.send_delivery_failure_email", return_value={"ok": True, "error": None}) as send, \
         patch("app.scheduler.record_admin_notice") as rec:
        scheduler._notify_and_record(rule, **{**KW, **kw})
    return gc, send, rec


def test_scheduler_sends_one_failure_mail_to_deliverable_admins_and_records_it():
    gc, send, rec = _run(_rule(), {"email": None, "slack": FAIL})
    send.assert_called_once()
    args = send.call_args.args
    assert args[0] == ["admin@corp.co.jp", "root@platform.co.jp"]   # example.com は除外
    assert args[1] == "Acme" and args[2] == "slack" and "HTTP 404" in args[3]
    assert args[4]["sensor_key"] == "temperature" and args[4]["device_id"] == "device-001"
    rec.assert_called_once()


def test_scheduler_does_not_repeat_within_the_same_cycle_for_the_same_rule():
    """グループのルールは同じサイクルで複数デバイス分の通知が走る。2台目以降で重複して送らない"""
    from app import scheduler
    rule = _rule()
    contacts = {"tenant_name": "Acme", "emails": ["admin@corp.co.jp"]}
    with patch("app.scheduler.notify", return_value={"email": None, "slack": FAIL}), \
         patch("app.scheduler.record_notify_status"), \
         patch("app.scheduler.get_tenant_admin_contacts", return_value=contacts), \
         patch("app.scheduler.send_delivery_failure_email", return_value={"ok": True, "error": None}) as send, \
         patch("app.scheduler.record_admin_notice"):
        for dev in ("device-001", "device-002", "device-003"):
            scheduler._notify_and_record(rule, **{**KW, "device_id": dev})
    assert send.call_count == 1


def test_scheduler_stays_quiet_while_already_failing_or_on_success():
    for status, results in (({"slack": {"ok": False, "error": "x"}}, {"email": None, "slack": FAIL}),
                            (None, {"email": None, "slack": OK})):
        gc, send, rec = _run(_rule(status), results)
        send.assert_not_called()
        rec.assert_not_called()


def test_scheduler_with_no_deliverable_recipient_sends_and_records_nothing():
    gc, send, rec = _run(_rule(), {"email": None, "slack": FAIL},
                         contacts={"tenant_name": "Acme", "emails": ["ops@example.com", "bad"]})
    send.assert_not_called()
    rec.assert_not_called()


def test_scheduler_survives_a_contacts_lookup_failure():
    gc, send, rec = _run(_rule(), {"email": None, "slack": FAIL}, contacts_error=RuntimeError("db down"))
    send.assert_not_called()
