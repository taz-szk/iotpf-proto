"""アラート通知の「テスト送信」。フォームに入力した(未保存の)通知先、または保存済みルールのSlack Webhookに、
テストメッセージを1通送り、成否を返す。プラットフォーム側・テナント側のエンドポイントが共通で使う。"""
import re
import threading
import time
from collections import defaultdict, deque
from typing import Literal, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import text

from app.database import SessionLocal
from app.models.public import Tenant
from app.services.audit import log_audit
from app.services.mailer import send_test_email
from app.services.slack_webhook import post_slack_message, slack_escape, validate_slack_webhook_url

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# 1ユーザーあたり、1分に5回まで(外部サービスへの送信を利用者が連打できないように)
_LIMIT = 5
_WINDOW_SEC = 60.0
_lock = threading.Lock()
_attempts: dict[str, deque] = defaultdict(deque)


def reset_rate_limits() -> None:
    with _lock:
        _attempts.clear()


def _check_rate_limit(key: str) -> None:
    now = time.monotonic()
    with _lock:
        q = _attempts[key]
        while q and q[0] <= now - _WINDOW_SEC:
            q.popleft()
        if len(q) >= _LIMIT:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                detail="テスト送信の回数が多すぎます。1分ほど待ってからもう一度お試しください")
        q.append(now)


class TestNotificationRequest(BaseModel):
    __test__ = False  # pytestがテストクラスと誤認しないように

    channel: Literal["slack", "email"]
    # slack: フォームに入力した未保存のURL、または保存済みルールのID(URLは画面に返さないため、サーバー側で探す)のどちらか一方
    slack_webhook_url: Optional[str] = None
    rule_id: Optional[str] = None
    # email: フォームに入力した宛先
    emails: Optional[list[EmailStr]] = Field(default=None, max_length=5)

    @field_validator("slack_webhook_url")
    @classmethod
    def _validate_url(cls, v):
        return validate_slack_webhook_url(v) if v is not None else None

    @field_validator("rule_id")
    @classmethod
    def _validate_rule_id(cls, v):
        if v is not None and not _UUID_RE.fullmatch(v.lower()):
            raise ValueError("rule_id must be a valid UUID")
        return v

    @model_validator(mode="after")
    def _check_channel_fields(self):
        if self.channel == "slack":
            if (self.slack_webhook_url is None) == (self.rule_id is None):
                raise ValueError("Specify exactly one of slack_webhook_url or rule_id")
        elif not self.emails:
            raise ValueError("emails is required")
        return self


def get_tenant_name(tenant_id: str) -> str:
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    return tenant.name if tenant else ""


def get_rule_slack_url(schema: str, rule_id: str) -> Optional[str]:
    """保存済みルールのSlack Webhook URL。ルールが無ければ404、URL未設定ならNone。"""
    with SessionLocal() as db:
        row = db.execute(
            text(f'SELECT slack_webhook_url FROM "{schema}".alert_rules WHERE id = :rid AND is_active = TRUE'),
            {"rid": rule_id},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return row.slack_webhook_url


def run_test_notification(
    actor_type: str, actor: dict, tenant_id: str, schema: str, req: TestNotificationRequest,
) -> dict:
    actor_id = str(actor.get("sub", ""))
    _check_rate_limit(f"{actor_type}:{actor_id}")

    tenant_name = get_tenant_name(tenant_id)
    sender = str(actor.get("email", ""))

    if req.channel == "slack":
        url = req.slack_webhook_url
        if url is None:
            url = get_rule_slack_url(schema, req.rule_id)
            if not url:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="このルールにはSlack Webhook URLが設定されていません")
        message = (
            ":test_tube: *[TEST]* IoTプラットフォームからのテスト通知です。\n"
            f"テナント: {slack_escape(tenant_name)}\n"
            f"送信者: {slack_escape(sender)}\n"
            "このメッセージが届いていれば、アラートのSlack通知は有効です。"
        )
        result = post_slack_message(url, message)
    else:
        result = send_test_email([str(e) for e in req.emails], tenant_name, sender)

    # 監査ログにはWebhook URL・宛先を残さない(チャンネルと結果だけ)
    log_audit(actor_type, actor_id, sender, "test_alert_notification",
              tenant_id=tenant_id, resource_type="alert_rule", resource_id=req.channel,
              result="success" if result["ok"] else "failure")
    return result
