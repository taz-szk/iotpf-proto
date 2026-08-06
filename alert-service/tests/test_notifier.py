from unittest.mock import patch, MagicMock
from app.notifier import send_alert_email

def test_send_alert_email_called():
    with patch("app.notifier.smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = lambda s: mock_server
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_alert_email(
            to_emails=["ops@example.com"], tenant_id="tenant-001",
            device_id="device-001", sensor_key="temperature",
            condition="above", threshold=80.0, current_value=85.3, severity="warning",
        )
    mock_smtp.assert_called_once()
    mock_server.sendmail.assert_called_once()

def test_send_alert_no_emails():
    with patch("app.notifier.smtplib.SMTP") as mock_smtp:
        send_alert_email(
            to_emails=[], tenant_id="tenant-001", device_id=None,
            sensor_key="temperature", condition="above",
            threshold=80.0, current_value=85.3, severity="warning",
        )
    mock_smtp.assert_not_called()

def test_send_resolved_email():
    with patch("app.notifier.smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = lambda s: mock_server
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_alert_email(
            to_emails=["ops@example.com"], tenant_id="tenant-001",
            device_id="device-001", sensor_key="temperature",
            condition="above", threshold=80.0, current_value=75.0,
            severity="warning", resolved=True,
        )
    args = mock_server.sendmail.call_args[0]
    assert "[RESOLVED]" in args[2]
