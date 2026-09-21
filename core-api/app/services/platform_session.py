import re

from fastapi import HTTPException, status

from app.database import SessionLocal
from app.models.public import PlatformUser

_UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def revalidate_platform_session(payload: dict) -> None:
    """Cookie JWT(Grafanaのauth_request用、最大24時間有効)の持ち主が今も有効な管理者で、
    ログイン後にパスワード変更(token_version更新)がされていないことを確認する。満たさない・
    DBに問い合わせられない場合は401。"""
    user_id = str(payload.get("sub", "")).lower()
    if not _UUID_RE.fullmatch(user_id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        with SessionLocal() as db:
            user = db.query(PlatformUser).filter(PlatformUser.id == user_id).first()
            valid = bool(user and user.is_active and payload.get("tok_ver") == user.token_version)
    except Exception:
        valid = False
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
