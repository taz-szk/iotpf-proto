import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings

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

    subject_prefix = "[RESOLVED]" if resolved else f"[{severity.upper()}]"
    subject = f"{subject_prefix} IoT Alert: {sensor_key}"
    if device_id:
        subject += f" / {device_id}"

    if resolved:
        body = f"Alert resolved.\nSensor: {sensor_key}\nDevice: {device_id or 'all'}\nTenant: {tenant_id}"
    else:
        body = (
            f"Alert triggered.\n"
            f"Sensor: {sensor_key}\n"
            f"Device: {device_id or 'all'}\n"
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
