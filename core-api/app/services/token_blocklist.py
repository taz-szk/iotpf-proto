import logging
import threading
import time

from sqlalchemy import text

from app.database import engine

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# {jti: expiry_monotonic} — DB問い合わせを減らすためのキャッシュ。正本はrevoked_tokensテーブル。
_revoked: dict[str, float] = {}


def _evict(now: float) -> None:
    expired = [j for j, exp in list(_revoked.items()) if exp <= now]
    for j in expired:
        del _revoked[j]


def _store(jti: str, exp_epoch: float) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO revoked_tokens (jti, expires_at) VALUES (:jti, to_timestamp(:exp)) "
                 "ON CONFLICT (jti) DO NOTHING"),
            {"jti": jti, "exp": exp_epoch},
        )
        # 期限切れの行はここで掃除する(失効させる操作は頻繁ではないので、専用ワーカーは置かない)
        conn.execute(text("DELETE FROM revoked_tokens WHERE expires_at < now()"))


def _lookup(jti: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM revoked_tokens WHERE jti = :jti AND expires_at > now()"),
            {"jti": jti},
        ).fetchone()
    return row is not None


def revoke_jti(jti: str, ttl_sec: float) -> None:
    now = time.monotonic()
    with _lock:
        _evict(now)
        _revoked[jti] = now + ttl_sec
    # 再起動後も失効を保つためDBにも残す。書けなくても、このプロセスでは失効済みとして扱える。
    try:
        _store(jti, time.time() + ttl_sec)
    except Exception:
        logger.error("Failed to persist revoked token", exc_info=True)


def is_revoked(jti: str) -> bool:
    now = time.monotonic()
    with _lock:
        _evict(now)
        if jti in _revoked:
            return True
    try:
        hit = _lookup(jti)
    except Exception:
        # 失効済みかどうか確かめられないときは、通さない(fail closed)
        logger.error("Failed to check revoked token", exc_info=True)
        return True
    if hit:
        with _lock:
            # DBの残り期限は分からないので、短い期間だけキャッシュして次の問い合わせに備える
            _revoked[jti] = now + 60.0
    return hit
