from datetime import timedelta
from app.services.auth import create_access_token, verify_token


def test_create_access_token_custom_expiry():
    token = create_access_token({"sub": "u1", "email": "a@b.com"}, expires_delta=timedelta(hours=24))
    payload = verify_token(token)
    assert payload["sub"] == "u1"
    assert payload["token_type"] == "access"


def test_create_access_token_default_expiry():
    token = create_access_token({"sub": "u2", "email": "b@b.com"})
    payload = verify_token(token)
    assert payload is not None
