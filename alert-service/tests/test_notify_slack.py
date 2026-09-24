"""アラートの通知先(メール / Slack Incoming Webhook)をルール単位で振り分けることのテスト。"""
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app import notifier
from app.notifier import notify, send_alert_slack

SECRET = "abcdEFGHijklMNOPqrstUVWX"
URL = "https://hooks.slack.com/" + "services/T01234567/B01234567/" + SECRET  # GitHubのpush protectionがダミーURLを本物と誤検出するため、連結して組み立てる
KW = dict(tenant_id="tenant-001", device_id="device-001", sensor_key="temperature",
          condition="above", threshold=80.0, current_value=85.3, severity="critical")


def _ok():
    r = MagicMock()
    r.status_code = 200
    r.text = "ok"
    return r


# ---------- send_alert_slack ----------

def test_posts_alert_text_to_the_webhook():
    with patch("app.notifier.httpx.post", return_value=_ok()) as post:
        send_alert_slack(URL, **KW)
    args, kwargs = post.call_args
    assert args[0] == URL
    text = kwargs["json"]["text"]
    for part in ("CRITICAL", "temperature", "device-001", "above", "80", "85.3", "tenant-001"):
        assert part in text
    assert kwargs["timeout"] == 10
    assert not kwargs.get("follow_redirects", False)


def test_resolved_message_says_resolved():
    with patch("app.notifier.httpx.post", return_value=_ok()) as post:
        send_alert_slack(URL, **{**KW, "current_value": 70.0}, resolved=True)
    assert "RESOLVED" in post.call_args.kwargs["json"]["text"]


def test_slack_control_characters_in_user_data_are_escaped():
    """センサー名・デバイスIDは利用者が決める値。<!channel> 等のメンションを注入させない。"""
    with patch("app.notifier.httpx.post", return_value=_ok()) as post:
        send_alert_slack(URL, **{**KW, "device_id": "<!channel> & <@U123>", "sensor_key": "<https://evil|click>"})
    text = post.call_args.kwargs["json"]["text"]
    assert "<!channel>" not in text and "<@U123>" not in text and "<https://evil" not in text
    assert "&lt;!channel&gt; &amp; &lt;@U123&gt;" in text


@pytest.mark.parametrize("bad", [
    "http://hooks.slack.com/services/T0/B0/x",
    "https://evil.example/services/T0/B0/x",
    "https://hooks.slack.com.evil.example/services/T0/B0/x",
    "http://169.254.169.254/latest/meta-data/",
    "",
])
def test_refuses_to_post_to_anything_but_slack(bad):
    """DBの値は信用しない(直接書き換えられた場合の多重防御)。"""
    with patch("app.notifier.httpx.post") as post:
        send_alert_slack(bad, **KW)
    post.assert_not_called()


def test_failure_never_raises_and_never_logs_the_url(capsys):
    err = httpx.ConnectError(f"connection failed for {URL}")
    with patch("app.notifier.httpx.post", side_effect=err):
        send_alert_slack(URL, **KW)  # 例外を外に出さない
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert "Slack send failed" in out.out + out.err


def test_non_200_is_logged_with_status_only(capsys):
    resp = MagicMock()
    resp.status_code = 404
    resp.text = f"no_service {URL}"
    with patch("app.notifier.httpx.post", return_value=resp):
        send_alert_slack(URL, **KW)
    out = capsys.readouterr()
    assert "404" in out.out
    assert SECRET not in out.out + out.err


# ---------- notify (ルール単位の振り分け) ----------

def _rule(emails=(), slack=None):
    return {"notify_emails": list(emails), "slack_webhook_url": slack}


def test_notify_email_only():
    with patch("app.notifier.send_alert_email") as email, patch("app.notifier.send_alert_slack") as slack:
        notify(_rule(emails=["a@example.com"]), **KW)
    email.assert_called_once()
    assert email.call_args.kwargs["to_emails"] == ["a@example.com"]
    slack.assert_not_called()


def test_notify_slack_only():
    with patch("app.notifier.send_alert_email") as email, patch("app.notifier.send_alert_slack") as slack:
        notify(_rule(slack=URL), **KW)
    slack.assert_called_once()
    assert slack.call_args.args[0] == URL
    email.assert_not_called()


def test_notify_both():
    with patch("app.notifier.send_alert_email") as email, patch("app.notifier.send_alert_slack") as slack:
        notify(_rule(emails=["a@example.com"], slack=URL), **KW)
    email.assert_called_once()
    slack.assert_called_once()


def test_notify_neither_sends_nothing():
    with patch("app.notifier.send_alert_email") as email, patch("app.notifier.send_alert_slack") as slack:
        notify(_rule(), **KW)
        notify({"notify_emails": None}, **KW)  # 旧データ(列なし・NULL)
    email.assert_not_called()
    slack.assert_not_called()


def test_one_channel_failing_does_not_block_the_other():
    with patch("app.notifier.send_alert_email", side_effect=RuntimeError("smtp down")) as email, \
         patch("app.notifier.send_alert_slack") as slack:
        notify(_rule(emails=["a@example.com"], slack=URL), **KW)
    slack.assert_called_once()

    with patch("app.notifier.send_alert_email") as email, \
         patch("app.notifier.send_alert_slack", side_effect=RuntimeError("slack down")):
        notify(_rule(emails=["a@example.com"], slack=URL), **KW)
    email.assert_called_once()


def test_notify_passes_resolved_flag_to_both():
    with patch("app.notifier.send_alert_email") as email, patch("app.notifier.send_alert_slack") as slack:
        notify(_rule(emails=["a@example.com"], slack=URL), **KW, resolved=True)
    assert email.call_args.kwargs["resolved"] is True
    assert slack.call_args.kwargs["resolved"] is True


# ---------- scheduler が notify を使う ----------

def _sched_patches(rule, should_alert, existing):
    return [
        patch("app.scheduler.get_active_alert_rules", return_value=[rule]),
        patch("app.scheduler._make_group_device_resolver", return_value=lambda g: []),
        patch("app.scheduler.evaluate_rule", return_value=(should_alert, 90.0)),
        patch("app.scheduler.get_unresolved_event", return_value=existing),
        patch("app.scheduler.create_alert_event", return_value="ev-1"),
        patch("app.scheduler.resolve_alert_event"),
        patch("app.scheduler.mark_event_notified"),
    ]


def _rule_row(**kw):
    base = {"id": "r1", "device_id": "device-001", "group_id": None, "sensor_key": "temperature",
            "condition": "above", "threshold": 80, "severity": "warning",
            "notify_emails": [], "slack_webhook_url": URL}
    base.update(kw)
    return base


def test_scheduler_notifies_via_rule_channels_on_trigger_and_on_resolve():
    from contextlib import ExitStack
    from app import scheduler

    for should_alert, existing, resolved in ((True, None, False), (False, {"id": "ev-0"}, True)):
        with ExitStack() as stack:
            for p in _sched_patches(_rule_row(), should_alert, existing):
                stack.enter_context(p)
            n = stack.enter_context(patch("app.scheduler.notify"))
            scheduler._evaluate_tenant("tenant-001", "org", "tok")
        n.assert_called_once()
        assert n.call_args.args[0]["slack_webhook_url"] == URL
        assert n.call_args.kwargs.get("resolved", False) is resolved


def test_scheduler_offline_alert_uses_notify():
    from contextlib import ExitStack
    from app import scheduler

    rule = _rule_row(condition="device_offline", sensor_key="device")
    with ExitStack() as stack:
        stack.enter_context(patch("app.scheduler.get_offline_devices", return_value=[{"device_id": "device-001"}]))
        stack.enter_context(patch("app.scheduler.mark_device_offline"))
        stack.enter_context(patch("app.scheduler.get_active_alert_rules", return_value=[rule]))
        stack.enter_context(patch("app.scheduler._make_group_device_resolver", return_value=lambda g: []))
        stack.enter_context(patch("app.scheduler.get_unresolved_event", return_value=None))
        stack.enter_context(patch("app.scheduler.create_alert_event", return_value="ev-1"))
        stack.enter_context(patch("app.scheduler.mark_event_notified"))
        n = stack.enter_context(patch("app.scheduler.notify"))
        scheduler._check_dead_devices("tenant-001")
    n.assert_called_once()
    assert n.call_args.kwargs["condition"] == "device_offline"
