import re

# Slack Incoming Webhook の形式に限る。送信先を自由にできると、サーバーから社内サービス等への
# リクエストを送らせられる(SSRF)ため、ホスト・パスまで固定する。
_SLACK_WEBHOOK_RE = re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+")
_MAX_LENGTH = 200


def validate_slack_webhook_url(url: str) -> str:
    url = url.strip()
    if len(url) > _MAX_LENGTH or not _SLACK_WEBHOOK_RE.fullmatch(url):
        raise ValueError("Slack Webhook URL must look like https://hooks.slack.com/services/T…/B…/…")
    return url


def mask_slack_webhook(url: str | None) -> dict:
    """APIレスポンス用。URLは秘密情報(知っていれば誰でもチャンネルへ投稿できる)なので、
    設定済みかどうかと末尾4文字だけを返す。"""
    if not url:
        return {"slack_configured": False, "slack_webhook_hint": None}
    return {"slack_configured": True, "slack_webhook_hint": "…" + url[-4:]}
