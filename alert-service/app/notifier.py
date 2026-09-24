import re
import smtplib
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
) -> None:
    if not to_emails:
        return

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
    except Exception as e:
        print(f"Email send failed: {e}")


def _slack_escape(value) -> str:
    # Slackは & < > を制御文字として解釈する。センサー名・デバイスIDは利用者が決める値なので、
    # <!channel> や <@U123> のようなメンション/リンクを注入されないようにエスケープする。
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
) -> None:
    if not webhook_url or not _SLACK_WEBHOOK_RE.fullmatch(webhook_url):
        print("Slack send skipped: webhook URL is not a Slack Incoming Webhook")
        return

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
        if resp.status_code != 200:
            print(f"Slack send failed: HTTP {resp.status_code}")
    except Exception as e:
        # 例外メッセージにWebhook URL(秘密情報)が含まれうるため、クラス名だけを記録する
        print(f"Slack send failed: {type(e).__name__}")


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
) -> None:
    """アラートルールに設定された通知先(メール・Slack)へ送る。片方が失敗してももう片方は送る。"""
    common = dict(tenant_id=tenant_id, device_id=device_id, sensor_key=sensor_key, condition=condition,
                  threshold=threshold, current_value=current_value, severity=severity, resolved=resolved)

    emails = list(rule.get("notify_emails") or [])
    if emails:
        try:
            send_alert_email(to_emails=emails, **common)
        except Exception as e:
            print(f"Email notify failed: {type(e).__name__}")

    webhook_url = rule.get("slack_webhook_url")
    if webhook_url:
        try:
            send_alert_slack(webhook_url, **common)
        except Exception as e:
            print(f"Slack notify failed: {type(e).__name__}")
