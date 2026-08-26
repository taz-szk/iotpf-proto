import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_failures: dict[str, list[float]] = defaultdict(list)

WINDOW_SEC = 900   # 15 minutes
MAX_FAILURES = 5


def _evict(key: str, now: float) -> None:
    cutoff = now - WINDOW_SEC
    _failures[key] = [t for t in _failures[key] if t > cutoff]


def is_rate_limited(key: str) -> bool:
    now = time.monotonic()
    with _lock:
        _evict(key, now)
        return len(_failures[key]) >= MAX_FAILURES


def record_failure(key: str) -> None:
    now = time.monotonic()
    with _lock:
        _evict(key, now)
        _failures[key].append(now)


def clear_failures(key: str) -> None:
    with _lock:
        _failures.pop(key, None)
