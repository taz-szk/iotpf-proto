from unittest.mock import patch, MagicMock
import pytest
from app.services.mailer import send_password_reset_email

def test_send_password_reset_email_sends_with_link():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance
        send_password_reset_email(
            "user@example.com", "Acme Corp", "https://example.local/admin/reset-password.html?token=abc",
        )
    assert instance.sendmail.called
    args = instance.sendmail.call_args[0]
    assert "https://example.local/admin/reset-password.html?token=abc" in args[2]

def test_send_password_reset_email_raises_on_failure():
    with patch("app.services.mailer.smtplib.SMTP", side_effect=ConnectionError("boom")):
        with pytest.raises(ConnectionError):
            send_password_reset_email("user@example.com", "Acme Corp", "https://x/reset?token=t")
