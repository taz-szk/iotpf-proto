import hashlib
import hmac
import re
import threading
import time

from fastapi import Cookie, HTTPException, status
from sqlalchemy import text

from app.database import engine
from app.services.auth import verify_token
from app.config import settings

_UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
# ユーザー削除・無効化・降格を、JWTの残存期間(最大24時間)を待たずに反映するための再検証結果の保持時間。
# 同一プロセス内の変更はforget_session()で即時に反映する。
_TTL = 10.0

_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[tuple[str, str] | None, float]] = {}


def password_fingerprint(password_hash: str) -> str:
    """パスワードハッシュから作る、JWTに入れる指紋。パスワードが変わると値も変わる。
    ハッシュそのものはJWT(持ち主に読める)に載せず、jwt_secretで鍵付きハッシュにして切り詰める。"""
    return hmac.new(settings.jwt_secret.encode(), password_hash.encode(), hashlib.sha256).hexdigest()[:24]


def _fetch_state(tenant_id: str, user_id: str) -> tuple[str, str] | None:
    """(現在のロール, パスワード指紋)。無効・削除済み・テナント停止・DBに問い合わせられないときはNone。"""
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(f'''SELECT u.role, u.password_hash FROM "{schema}".users u
                         JOIN public.tenants t ON t.id = CAST(:tid AS uuid)
                         WHERE u.id = CAST(:uid AS uuid) AND u.is_active = TRUE AND t.status = 'active' '''),
                {"tid": tenant_id, "uid": user_id},
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row.role, password_fingerprint(row.password_hash)


def _current_state(tenant_id: str, user_id: str) -> tuple[str, str] | None:
    key = (tenant_id, user_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[1] > now:
            return hit[0]

    state = _fetch_state(tenant_id, user_id)
    if state is not None:
        # 見つからない/確認できない結果はキャッシュしない(直後に復旧・復活しても待たせない)
        with _lock:
            _cache[key] = (state, now + _TTL)
    return state


def current_password_fingerprint(tenant_id: str, user_id: str) -> str | None:
    """ログインの最終段階(TOTP検証後)でJWTに入れる指紋を、DBの最新値から得る。"""
    state = _fetch_state(tenant_id.lower(), user_id.lower())
    return state[1] if state else None


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
    state = _current_state(tenant_id, user_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    role, pwv = state
    # ログイン後にパスワードが変更・リセットされていれば、そのJWTは使えない。pwvを持たない旧JWTも同様
    if not hmac.compare_digest(str(payload.get("pwv", "")), pwv):
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
