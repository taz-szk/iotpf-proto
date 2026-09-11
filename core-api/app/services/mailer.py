import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings


def send_bill_shock_email(
    to_emails: list[str], tenant_name: str, target_year_month: str,
    total_amount: int, threshold_amount: int,
) -> None:
    """ビルショック通知メールを送信する。to_emailsが空なら何もしない。
    送信失敗は例外を伝播させずログ出力のみ（呼び出し元の請求バッチを止めないため）。"""
    if not to_emails:
        return

    tenant_name_clean = str(tenant_name).replace("\r", "").replace("\n", "")
    subject = f"[Bill Shock Alert] {tenant_name_clean} / {target_year_month}"
    body = (
        f"Bill shock threshold exceeded.\n"
        f"Tenant: {tenant_name_clean}\n"
        f"Target month: {target_year_month}\n"
        f"Current total amount: {total_amount}\n"
        f"Threshold: {threshold_amount}\n"
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
        print(f"Bill shock email send failed: {e}")
