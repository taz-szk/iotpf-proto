import pytest
from fastapi import HTTPException

from app.services.password_policy import MIN_LENGTH, validate_password


def test_minimum_length_is_12():
    assert MIN_LENGTH == 12


def test_accepts_12_chars():
    validate_password("a" * 12)


@pytest.mark.parametrize("pw", ["", "short", "a" * 11])
def test_rejects_too_short(pw):
    with pytest.raises(HTTPException) as e:
        validate_password(pw)
    assert e.value.status_code == 400
    assert "12" in e.value.detail


def test_rejects_over_bcrypt_limit():
    # bcryptは72バイトを超える部分を扱えない(新しいbcryptではエラーになる)ので、500ではなく400で断る
    validate_password("a" * 72)
    with pytest.raises(HTTPException) as e:
        validate_password("a" * 73)
    assert e.value.status_code == 400
    # 日本語は1文字3バイト
    with pytest.raises(HTTPException):
        validate_password("あ" * 25)


def test_rejects_password_equal_to_email():
    with pytest.raises(HTTPException) as e:
        validate_password("admin@example.com", email="admin@example.com")
    assert e.value.status_code == 400
