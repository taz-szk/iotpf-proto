"""Slack通知が届かなかったとき、管理者へメールで知らせるための判定と宛先の絞り込み。

メール(SES等)のバウンス率・送信量に影響しないよう、次のように絞る。
- 「失敗に変わったとき」の1回だけ送る(失敗が続く間は送らない)。成功と失敗を繰り返しても、同じルールに1時間の間隔を空ける
- 届かない宛先(形式の不正、予約ドメイン)は除外し、1回の宛先は上限まで
- メールチャンネル自体の失敗では送らない(メールが不調なときに、メールを重ねない)
"""
import re
from datetime import datetime, timedelta, timezone

MAX_RECIPIENTS = 10
COOLDOWN = timedelta(hours=1)

# ローカル部・ドメインに空白と@を含まず、ドメインにドットが1つ以上ある形式
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")
# RFC 2606 / 6761 の予約ドメイン。実在しないため、送るとバウンスになる
_RESERVED_DOMAINS = ("example.com", "example.net", "example.org")
_RESERVED_SUFFIXES = (".test", ".example", ".invalid", ".local", ".localhost", ".localdomain")


def _is_deliverable(address: str) -> bool:
    if not _EMAIL_RE.fullmatch(address):
        return False
    domain = address.rsplit("@", 1)[1]
    if any(domain == d or domain.endswith("." + d) for d in _RESERVED_DOMAINS):
        return False
    return not domain.endswith(_RESERVED_SUFFIXES)


def filter_deliverable(emails) -> list[str]:
    """宛先を小文字にそろえて重複を除き、届かないアドレスを外して、上限までを順序を保って返す。"""
    seen: set[str] = set()
    out: list[str] = []
    for e in emails or []:
        if not isinstance(e, str):
            continue
        addr = e.strip().lower()
        if not addr or addr in seen or not _is_deliverable(addr):
            continue
        seen.add(addr)
        out.append(addr)
        if len(out) >= MAX_RECIPIENTS:
            break
    return out


def _parse_time(value) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def decide_failed_channels(previous_status, results, now: datetime) -> list[str]:
    """管理者へ知らせるべき、失敗に変わったチャンネル。previous_statusはこの通知の前のnotify_status。"""
    if not isinstance(results, dict):
        return []
    slack = results.get("slack")
    if not isinstance(slack, dict) or slack.get("ok") is not False:
        return []

    prev = previous_status if isinstance(previous_status, dict) else {}
    prev_slack = prev.get("slack")
    if isinstance(prev_slack, dict) and prev_slack.get("ok") is False:
        return []  # すでに失敗が続いている(通知済み)

    notice = prev.get("admin_notice")
    last = _parse_time(notice.get("at")) if isinstance(notice, dict) else None
    if last is not None and now - last < COOLDOWN:
        return []  # 成功と失敗を繰り返していても、短時間に何通も送らない
    return ["slack"]
