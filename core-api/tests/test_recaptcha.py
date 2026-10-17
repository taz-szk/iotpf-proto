from unittest.mock import patch, MagicMock
from app.services.recaptcha import verify_recaptcha


def test_verify_recaptcha_accepts_high_score():
    resp = MagicMock()
    resp.json.return_value = {"success": True, "score": 0.9}
    with patch("app.services.recaptcha.httpx.post", return_value=resp):
        assert verify_recaptcha("good-token") is True


def test_verify_recaptcha_rejects_low_score():
    resp = MagicMock()
    resp.json.return_value = {"success": True, "score": 0.1}
    with patch("app.services.recaptcha.httpx.post", return_value=resp):
        assert verify_recaptcha("bot-token") is False


def test_verify_recaptcha_rejects_when_success_false():
    resp = MagicMock()
    resp.json.return_value = {"success": False, "error-codes": ["invalid-input-response"]}
    with patch("app.services.recaptcha.httpx.post", return_value=resp):
        assert verify_recaptcha("bad-token") is False


def test_verify_recaptcha_rejects_on_network_error():
    with patch("app.services.recaptcha.httpx.post", side_effect=Exception("timeout")):
        assert verify_recaptcha("any-token") is False
