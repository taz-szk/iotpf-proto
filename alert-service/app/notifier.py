import re
import smtplib
import socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import httpx

from app.config import settings

# core-api と同じ検証。DBの値は信用せず、送信の直前にも確認する(送信先の自由指定=SSRFの多重防御)。
_SLACK_WEBHOOK_RE = re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+")

def send_alert_email(
    to_emails: list[str],
    tenant_id: str,
    device_id: str | None,
    sensor_key: str,
    condition: str,
    threshold: float | None,
    current_value: float | None,
    severity: str,
    resolved: bool = False,
) -> dict | None:
    """送信結果 {"ok", "error"} を返す。宛先が無く送信を試みなかった場合は None。"""
    if not to_emails:
        return None

    # Sanitize values that go into email headers
    sensor_key = str(sensor_key).replace("\r", "").replace("\n", "")
    device_id_clean = str(device_id).replace("\r", "").replace("\n", "") if device_id else None

    subject_prefix = "[RESOLVED]" if resolved else f"[{severity.upper()}]"
    subject = f"{subject_prefix} IoT Alert: {sensor_key}"
    if device_id_clean:
        subject += f" / {device_id_clean}"

    if resolved:
        body = f"Alert resolved.\nSensor: {sensor_key}\nDevice: {device_id_clean or 'all'}\nTenant: {tenant_id}"
    else:
        body = (
            f"Alert triggered.\n"
            f"Sensor: {sensor_key}\n"
            f"Device: {device_id_clean or 'all'}\n"
            f"Condition: {condition} {threshold}\n"
            f"Current value: {current_value}\n"
            f"Tenant: {tenant_id}"
        )

    msg = MIMEMultipart()
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_user:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from, to_emails, msg.as_string())
        return {"ok": True, "error": None}
    except Exception as e:
        # SMTPのエラーメッセージには宛先アドレスが含まれうるため、種類だけを記録・返却する
        print(f"Email send failed: {type(e).__name__}")
        return {"ok": False, "error": _describe_smtp_exception(e)}


def _describe_smtp_exception(e: Exception) -> str:
    """利用者に見せてよい説明。SMTPのエラーメッセージには宛先アドレスが含まれうるため、種類(クラス名)だけを添える。"""
    if isinstance(e, socket.gaierror):
        hint = "SMTPサーバーのホスト名を解決できません"
    elif isinstance(e, ConnectionError):
        hint = "SMTPサーバーに接続できません"
    elif isinstance(e, (TimeoutError, socket.timeout)):
        hint = "SMTPサーバーへの接続がタイムアウトしました"
    elif isinstance(e, smtplib.SMTPAuthenticationError):
        hint = "SMTPの認証に失敗しました。ユーザー名とパスワードを確認してください"
    elif isinstance(e, smtplib.SMTPRecipientsRefused):
        hint = "宛先がSMTPサーバーに受け付けられませんでした"
    else:
        hint = "メール送信に失敗しました"
    return f"{hint}（{type(e).__name__}）"


def _slack_escape(value) -> str:
    # Slackは & < > を制御文字として解釈する。センサー名・デバイスIDは利用者が決める値なので、
    # <!channel> や <@U123> のようなメンション/リンクを注入されないようにエスケープする。
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _describe_slack_http_failure(status: int, body: str) -> str:
    body = (body or "").strip()
    hint = {
        "no_team": "Webhook URLが正しくありません",
        "no_service": "Webhookが存在しない、または削除されています",
        "no_service_id": "Webhookが存在しない、または削除されています",
        "invalid_token": "Webhookが無効化されています",
        "channel_not_found": "投稿先のチャンネルが見つかりません",
        "channel_is_archived": "投稿先のチャンネルがアーカイブされています",
        "action_prohibited": "Slackの管理設定により、このWebhookからの投稿が制限されています",
        "no_text": "メッセージが空です",
        "invalid_payload": "メッセージがSlackに受け付けられませんでした",
    }.get(body)
    if hint is None and status == 429:
        hint = "Slackの送信制限に達しました。しばらくしてから再度お試しください"
    return f"HTTP {status}（{hint}）" if hint else f"HTTP {status}"


def _describe_slack_exception(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "タイムアウトしました（10秒）"
    if isinstance(e, httpx.NetworkError):
        return f"Slackに接続できません（{type(e).__name__}）"
    return f"送信エラー（{type(e).__name__}）"


def send_alert_slack(
    webhook_url: str,
    tenant_id: str,
    device_id: str | None,
    sensor_key: str,
    condition: str,
    threshold: float | None,
    current_value: float | None,
    severity: str,
    resolved: bool = False,
) -> dict:
    """送信結果 {"ok", "error"} を返す。errorには利用者に見せてよい説明だけを入れる(URLは含めない)。"""
    if not webhook_url or not _SLACK_WEBHOOK_RE.fullmatch(webhook_url):
        print("Slack send skipped: webhook URL is not a Slack Incoming Webhook")
        return {"ok": False, "error": "Webhook URLの形式が正しくありません"}

    device = _slack_escape(device_id) if device_id else "all"
    if resolved:
        text = (f":white_check_mark: *[RESOLVED]* IoT Alert: {_slack_escape(sensor_key)} / {device}\n"
                f"Tenant: {_slack_escape(tenant_id)}")
    else:
        text = (f":rotating_light: *[{_slack_escape(severity).upper()}]* IoT Alert: {_slack_escape(sensor_key)} / {device}\n"
                f"Condition: {_slack_escape(condition)} {threshold}\n"
                f"Current value: {current_value}\n"
                f"Tenant: {_slack_escape(tenant_id)}")

    try:
        resp = httpx.post(webhook_url, json={"text": text}, timeout=10)
    except Exception as e:
        # 例外メッセージにWebhook URL(秘密情報)が含まれうるため、クラス名だけを記録・返却する
        print(f"Slack send failed: {type(e).__name__}")
        return {"ok": False, "error": _describe_slack_exception(e)}
    if resp.status_code != 200:
        print(f"Slack send failed: HTTP {resp.status_code}")
        return {"ok": False, "error": _describe_slack_http_failure(resp.status_code, resp.text)}
    return {"ok": True, "error": None}


def notify(
    rule: dict,
    tenant_id: str,
    device_id: str | None,
    sensor_key: str,
    condition: str,
    threshold: float | None,
    current_value: float | None,
    severity: str,
    resolved: bool = False,
) -> dict:
    """アラートルールに設定された通知先(メール・Slack)へ送る。片方が失敗してももう片方は送る。
    チャンネルごとの結果 {"email": {...}|None, "slack": {...}|None} を返す(送らなかったチャンネルはNone)。"""
    common = dict(tenant_id=tenant_id, device_id=device_id, sensor_key=sensor_key, condition=condition,
                  threshold=threshold, current_value=current_value, severity=severity, resolved=resolved)

    results: dict = {"email": None, "slack": None}

    emails = list(rule.get("notify_emails") or [])
    if emails:
        try:
            results["email"] = send_alert_email(to_emails=emails, **common)
        except Exception as e:
            print(f"Email notify failed: {type(e).__name__}")
            results["email"] = {"ok": False, "error": _describe_smtp_exception(e)}

    webhook_url = rule.get("slack_webhook_url")
    if webhook_url:
        try:
            results["slack"] = send_alert_slack(webhook_url, **common)
        except Exception as e:
            print(f"Slack notify failed: {type(e).__name__}")
            results["slack"] = {"ok": False, "error": f"送信エラー（{type(e).__name__}）"}

    return results
