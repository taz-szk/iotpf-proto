from unittest.mock import patch, MagicMock
from app.services.totp import generate_totp_secret, verify_totp_code
import pyotp

def test_totp_verify_roundtrip():
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_totp_code(secret, code) is True

def test_totp_verify_wrong_code():
    secret = generate_totp_secret()
    assert verify_totp_code(secret, "000000") is False
