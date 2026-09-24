import smtplib
import socket
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


def send_test_email(to_emails: list[str], tenant_name: str, sender_email: str) -> dict:
    """アラート通知のテストメールを送り、{"ok", "error"} を返す。errorに宛先アドレスは含めない。"""
    tenant_name_clean = str(tenant_name).replace("\r", "").replace("\n", "")
    sender_clean = str(sender_email).replace("\r", "").replace("\n", "")
    msg = MIMEMultipart()
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = f"[TEST] IoT Alert notification test / {tenant_name_clean}"
    msg.attach(MIMEText(
        "This is a test notification from the IoT platform.\n"
        f"Tenant: {tenant_name_clean}\n"
        f"Requested by: {sender_clean}\n"
        "If you received this message, alert email notification is working.\n",
        "plain",
    ))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_user:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from, to_emails, msg.as_string())
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": _describe_smtp_exception(e)}
