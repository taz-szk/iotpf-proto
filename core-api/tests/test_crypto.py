import pytest

from app.services.crypto import decrypt_secret, encrypt_secret


def test_encrypt_then_decrypt_roundtrip():
    ciphertext = encrypt_secret("super-secret-value")
    assert ciphertext != "super-secret-value"
    assert decrypt_secret(ciphertext) == "super-secret-value"


def test_ciphertext_does_not_contain_the_plaintext():
    ciphertext = encrypt_secret("keyId-abcdefg12345")
    assert "keyId-abcdefg12345" not in ciphertext


def test_decrypt_with_wrong_key_fails(monkeypatch):
    from app.services import crypto
    ciphertext = encrypt_secret("value-a")
    monkeypatch.setattr(crypto.settings, "secrets_encryption_key", "a-completely-different-32-char-key!")
    with pytest.raises(Exception):
        decrypt_secret(ciphertext)


def test_decrypt_garbage_raises():
    with pytest.raises(Exception):
        decrypt_secret("not-a-valid-fernet-token")
