import threading
import time

_lock = threading.Lock()
# {jti: expiry_monotonic}
_revoked: dict[str, float] = {}


def _evict(now: float) -> None:
    expired = [j for j, exp in list(_revoked.items()) if exp <= now]
    for j in expired:
        del _revoked[j]


def revoke_jti(jti: str, ttl_sec: float) -> None:
    now = time.monotonic()
    with _lock:
        _evict(now)
        _revoked[jti] = now + ttl_sec


def is_revoked(jti: str) -> bool:
    now = time.monotonic()
    with _lock:
        _evict(now)
        return jti in _revoked
