import re
from typing import Callable

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str, is_taken: Callable[[str], bool]) -> str:
    """表示名からURL/DB向けのslugを作る。英数字以外は捨てるため、全て非ASCII文字の
    名前は空になりうる — その場合は"tenant"にフォールバックする。
    is_takenでTrueが返るたびに連番を付けて再試行する。"""
    base = _NON_ALNUM.sub("-", name.lower()).strip("-")
    if not base:
        base = "tenant"

    candidate = base
    suffix = 1
    while is_taken(candidate):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate
