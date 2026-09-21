from fastapi import HTTPException, status

MIN_LENGTH = 12
# bcryptは72バイトまでしか扱えない。超える入力はここで断る(そのままだとハッシュ化で500になる)
_MAX_BYTES = 72


def validate_password(password: str, email: str | None = None) -> None:
    """新しく設定するパスワードの検証。ログイン時の照合には使わない(既存の短いパスワードでも入れる)。"""
    if len(password) < MIN_LENGTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"Password must be at least {MIN_LENGTH} characters")
    if len(password.encode("utf-8")) > _MAX_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"Password must be at most {_MAX_BYTES} bytes")
    if email and password.lower() == email.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Password must not be the same as the email")
