from unittest.mock import patch, MagicMock
import pytest
from app.services.mailer import send_signup_verification_email, send_trial_expiry_warning_email


def test_send_signup_verification_email_sends_with_link():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance
        send_signup_verification_email(
            "user@example.com", "Acme Corp", "https://example.local/signup/verify?token=abc",
        )
    assert instance.sendmail.called
    args = instance.sendmail.call_args[0]
    assert "https://example.local/signup/verify?token=abc" in args[2]


def test_send_signup_verification_email_raises_on_failure():
    with patch("app.services.mailer.smtplib.SMTP", side_effect=ConnectionError("boom")):
        with pytest.raises(ConnectionError):
            send_signup_verification_email("user@example.com", "Acme Corp", "https://x/verify?token=t")


def test_send_trial_expiry_warning_email_sends():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance
        send_trial_expiry_warning_email("admin@example.com", "Acme Corp", "2026-12-01")
    assert instance.sendmail.called
    args = instance.sendmail.call_args[0]
    assert "2026-12-01" in args[2]


def test_send_trial_expiry_warning_email_swallows_failure():
    with patch("app.services.mailer.smtplib.SMTP", side_effect=ConnectionError("boom")):
        send_trial_expiry_warning_email("admin@example.com", "Acme Corp", "2026-12-01")
    # 例外が伝播しなければテストは成功で終わる
