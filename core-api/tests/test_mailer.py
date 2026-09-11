from unittest.mock import patch, MagicMock

from app.services.mailer import send_bill_shock_email


def test_send_bill_shock_email_sends_when_recipients_present():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = lambda s: mock_server
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_bill_shock_email(
            to_emails=["admin@example.com"], tenant_name="Acme Corp",
            target_year_month="2026-09", total_amount=120000, threshold_amount=100000,
        )
    mock_smtp.assert_called_once()
    mock_server.sendmail.assert_called_once()
    args = mock_server.sendmail.call_args[0]
    assert "admin@example.com" in args[1]
    assert "2026-09" in args[2]
    assert "120000" in args[2]
    assert "100000" in args[2]


def test_send_bill_shock_email_no_recipients_is_noop():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        send_bill_shock_email(
            to_emails=[], tenant_name="Acme Corp",
            target_year_month="2026-09", total_amount=120000, threshold_amount=100000,
        )
    mock_smtp.assert_not_called()


def test_send_bill_shock_email_swallows_smtp_errors():
    with patch("app.services.mailer.smtplib.SMTP", side_effect=OSError("connection refused")):
        send_bill_shock_email(
            to_emails=["admin@example.com"], tenant_name="Acme Corp",
            target_year_month="2026-09", total_amount=120000, threshold_amount=100000,
        )
    # 例外が外に伝播しなければ成功
