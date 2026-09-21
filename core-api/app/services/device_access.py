import re
import threading
import time

from sqlalchemy import text

from app.database import SessionLocal

_UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_POSITIVE_TTL = 30.0
# 再登録直後の新デバイスを長く弾かないよう、否定結果は短く保持する
_NEGATIVE_TTL = 5.0

_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[bool, float]] = {}


def is_device_registered(tenant_id: str, device_id: str) -> bool:
    """devicesテーブルに登録済みか。削除済みデバイスは証明書が有効でもMQTT接続・送信を許可しないために使う。
    DBに問い合わせられない場合は許可しない(fail closed)。"""
    if not _UUID_RE.fullmatch(tenant_id.lower()):
        return False
    key = (tenant_id.lower(), device_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[1] > now:
            return hit[0]

    schema = f"tenant_{tenant_id.lower().replace('-', '_')}"
    try:
        with SessionLocal() as db:
            found = db.execute(
                text(f'SELECT 1 FROM "{schema}".devices WHERE device_id = :did'),
                {"did": device_id},
            ).first() is not None
    except Exception:
        return False

    with _lock:
        _cache[key] = (found, now + (_POSITIVE_TTL if found else _NEGATIVE_TTL))
    return found


def forget_device(tenant_id: str, device_id: str) -> None:
    with _lock:
        _cache.pop((tenant_id.lower(), device_id), None)
