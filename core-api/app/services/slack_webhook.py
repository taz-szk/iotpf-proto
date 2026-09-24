import re

import httpx

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


def slack_escape(value) -> str:
    """Slackは & < > を制御文字として解釈する(<!channel> 等のメンション注入を防ぐ)。"""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _describe_http_failure(status: int, body: str) -> str:
    hint = {
        "no_team": "Webhook URLが正しくありません",
        "no_service": "Webhookが存在しない、または削除されています",
        "no_service_id": "Webhookが存在しない、または削除されています",
        "invalid_token": "Webhookが無効化されています",
        "channel_not_found": "投稿先のチャンネルが見つかりません",
        "channel_is_archived": "投稿先のチャンネルがアーカイブされています",
        "action_prohibited": "Slackの管理設定により、このWebhookからの投稿が制限されています",
        "no_text": "メッセージが空です",
        "invalid_payload": "メッセージがSlackに受け付けられませんでした",
    }.get((body or "").strip())
    if hint is None and status == 429:
        hint = "Slackの送信制限に達しました。しばらくしてから再度お試しください"
    return f"HTTP {status}（{hint}）" if hint else f"HTTP {status}"


def _describe_exception(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "タイムアウトしました（10秒）"
    if isinstance(e, httpx.NetworkError):
        return f"Slackに接続できません（{type(e).__name__}）"
    return f"送信エラー（{type(e).__name__}）"


def post_slack_message(url: str, text: str) -> dict:
    """Incoming Webhookへ投稿し、{"ok", "error"} を返す。errorは利用者に見せてよい説明で、URLは含まない
    (例外メッセージにURLが含まれうるため、クラス名だけを使う)。"""
    if not _SLACK_WEBHOOK_RE.fullmatch(url or ""):
        return {"ok": False, "error": "Webhook URLの形式が正しくありません"}
    try:
        resp = httpx.post(url, json={"text": text}, timeout=10)
    except Exception as e:
        return {"ok": False, "error": _describe_exception(e)}
    if resp.status_code != 200:
        return {"ok": False, "error": _describe_http_failure(resp.status_code, resp.text)}
    return {"ok": True, "error": None}
