import re
import threading
import time

from fastapi import Cookie, HTTPException, status
from sqlalchemy import text

from app.database import engine
from app.services.auth import verify_token

_UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
# ユーザー削除・無効化・降格を、JWTの残存期間(最大24時間)を待たずに反映するための再検証結果の保持時間。
# 同一プロセス内の変更はforget_session()で即時に反映する。
_TTL = 10.0

_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[str | None, float]] = {}


def _current_role(tenant_id: str, user_id: str) -> str | None:
    key = (tenant_id, user_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[1] > now:
            return hit[0]

    schema = f"tenant_{tenant_id.replace('-', '_')}"
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(f'''SELECT u.role FROM "{schema}".users u
                         JOIN public.tenants t ON t.id = CAST(:tid AS uuid)
                         WHERE u.id = CAST(:uid AS uuid) AND u.is_active = TRUE AND t.status = 'active' '''),
                {"tid": tenant_id, "uid": user_id},
            ).fetchone()
    except Exception:
        return None
    role = row.role if row else None
    with _lock:
        _cache[key] = (role, now + _TTL)
    return role


def forget_session(tenant_id: str, user_id: str) -> None:
    with _lock:
        _cache.pop((tenant_id.lower(), user_id.lower()), None)


def revalidate_session(payload: dict) -> dict:
    """JWTの持ち主が今もそのテナントの有効なユーザーか確認し、ロールをDBの最新値に置き換えた
    payloadを返す。存在しない・無効化済み・テナント削除済み・DBに問い合わせられない場合は401。"""
    tenant_id = str(payload.get("tenant_id", "")).lower()
    user_id = str(payload.get("sub", "")).lower()
    if not _UUID_RE.fullmatch(tenant_id) or not _UUID_RE.fullmatch(user_id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    role = _current_role(tenant_id, user_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return {**payload, "role": role}


def require_tenant_session(iot_token: str = Cookie(default=None)) -> dict:
    """テナントユーザーのCookie認証。公開ダッシュボード用の匿名トークン(public)は拒否する。"""
    if not iot_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = verify_token(iot_token)
    if (not payload or payload.get("type") != "tenant" or payload.get("public")
            or payload.get("token_type") != "access"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return revalidate_session(payload)
