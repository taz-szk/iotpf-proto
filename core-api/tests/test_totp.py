from app.services.totp import generate_totp_secret, get_totp_uri, verify_totp_code
import pyotp

def test_generate_secret_is_base32():
    secret = generate_totp_secret()
    assert len(secret) == 32
    assert secret.isalpha() or secret.replace("=", "").isalnum()

def test_get_totp_uri_format():
    secret = generate_totp_secret()
    uri = get_totp_uri(secret, "test@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "IoTAir-X" in uri

def test_verify_totp_code_valid():
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_totp_code(secret, code) is True

def test_verify_totp_code_invalid():
    secret = generate_totp_secret()
    assert verify_totp_code(secret, "000000") is False
