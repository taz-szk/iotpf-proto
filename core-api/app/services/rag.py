import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.rag import AgentPendingAction
from app.services.assistant_settings import get_assistant_settings
from app.services.audit import write_audit_log
from app.services.ollama_client import chat, embed

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 2
_TOP_K_CHUNKS = 3
_PENDING_ACTION_TTL_MINUTES = 10

_SYSTEM_PROMPT = (
    "あなたはIoTプラットフォームの操作・仕様に関する質問に答えるアシスタントです。"
    "提供されたドキュメントの抜粋を参考に、日本語で簡潔に回答してください。"
    "プラットフォームの現在の情報が必要な場合はツールを使ってください。"
    "ユーザーが操作の実行（トークン発行・設定変更・アラート作成等）を依頼した場合は、"
    "該当するツールを呼び出してください（実際の実行はユーザーの確認後に行われます）。"
    "手順や複数の項目を説明する場合は、改行や箇条書き（「- 」で始める等）を使って読みやすく整形してください。"
)


def _search_chunks(db: Session, question_embedding: list[float]) -> list:
    embedding_str = "[" + ",".join(str(x) for x in question_embedding) + "]"
    result = db.execute(
        text("""
            SELECT source_path, heading, content, embedding <=> :query_embedding AS distance
            FROM doc_chunks
            ORDER BY distance
            LIMIT :top_k
        """),
        {"query_embedding": embedding_str, "top_k": _TOP_K_CHUNKS},
    )
    return result.fetchall()


def _tool_schemas(tools: list) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _find_tool(tools: list, name: str):
    for t in tools:
        if t.name == name:
            return t
    return None


def _sanitize_tool_args(tool_args: dict) -> dict:
    """モデルが返したtool_argsから'tenant_id'/'payload'キーを除去する。
    ハンドラのtenant_id/payloadは常に呼び出し元の認証済みコンテキスト由来の値のみを使い、
    モデルが返した値は信用しない（agent_pending_actionsへの永続化前にも適用する）。"""
    return {k: v for k, v in tool_args.items() if k not in ("tenant_id", "payload")}


def _default_tools() -> list:
    """PF管理者向けのグローバルTOOLSを遅延importで取得する。
    モジュールトップレベルでrag_toolsをimportすると、rag_tools側がtenant_portal.pyを
    (直接・tenant経由の両方で)逆importする構造と組み合わさって循環importが発生するため、
    呼び出し時まで遅延させる（最終レビューで発見・修正）。"""
    from app.services.rag_tools import TOOLS
    return TOOLS


def answer_question(
    db: Session, tenant_id: str, message: str, requested_by: str,
    tools: list | None = None, payload: dict | None = None,
) -> dict:
    """質問に回答する。読み取り専用ツールは即実行し、アクション実行ツールは
    pending_actionとして保存し確認待ちにする。
    tools省略時はPF管理者向けのグローバルTOOLSを使う。payloadはテナント向け呼び出し時のみ
    認証済みJWTペイロードを渡す（PF管理者向けはNoneのまま、ハンドラにpayloadキーワード自体を渡さない）。
    戻り値: {"answer": str, "sources": list[dict], "pending_action": dict | None}"""
    ollama_settings = get_assistant_settings(db)
    ollama_url = ollama_settings.ollama_url
    chat_model = ollama_settings.ollama_chat_model
    embed_model = ollama_settings.ollama_embed_model

    tools = list(tools if tools is not None else _default_tools())
    question_embedding = embed(ollama_url, embed_model, message)
    chunks = _search_chunks(db, question_embedding)

    context_text = "\n\n".join(
        f"[出典: {c.source_path} - {c.heading or '(見出しなし)'}]\n{c.content}" for c in chunks
    )
    sources = [{"source_path": c.source_path, "heading": c.heading} for c in chunks]

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"参考ドキュメント:\n{context_text}\n\n質問: {message}"},
    ]

    for _ in range(_MAX_TOOL_ROUNDS):
        response = chat(ollama_url, chat_model, messages=messages, tools=_tool_schemas(tools))

        if not response.get("tool_calls"):
            return {"answer": response.get("content") or "", "sources": sources, "pending_action": None}

        tool_call = response["tool_calls"][0]
        tool_call_id = tool_call.get("id")
        tool_name = tool_call["function"]["name"]
        tool_args = _sanitize_tool_args(json.loads(tool_call["function"]["arguments"] or "{}"))
        tool = _find_tool(tools, tool_name)

        if tool is None:
            messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps({"error": "unknown tool"})})
            continue

        if tool.read_only:
            handler_kwargs = {"tenant_id": tenant_id, **tool_args}
            if payload is not None:
                handler_kwargs["payload"] = payload
            try:
                result = tool.handler(**handler_kwargs)
            except Exception as e:
                logger.warning("assistant tool %s failed: %s", tool_name, e)
                result = {"error": f"ツール実行に失敗しました: {type(e).__name__}"}
            messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result, default=str)})
            continue

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_PENDING_ACTION_TTL_MINUTES)
        pending = AgentPendingAction(
            tenant_id=tenant_id, tool_name=tool_name, tool_args=tool_args,
            requested_by=requested_by, expires_at=expires_at,
        )
        db.add(pending)
        db.commit()
        db.refresh(pending)
        return {
            "answer": f"「{tool.description}」を実行しますか？",
            "sources": sources,
            "pending_action": {
                "pending_action_id": str(pending.id),
                "tool_name": tool_name,
                "tool_args": tool_args,
            },
        }

    # 往復上限に達した場合、ツールを渡さずもう一度呼んで最終回答を生成させる
    # （仕様書§4.1.5: 「そこまでに得られた情報だけで最終回答を生成させる」）
    final = chat(ollama_url, chat_model, messages=messages)
    return {"answer": final.get("content") or "", "sources": sources, "pending_action": None}


def execute_pending_action(
    db: Session, tenant_id: str, pending_action_id: str,
    tools: list | None = None, payload: dict | None = None,
):
    """未確認アクションを実行する。見つからない/期限切れならNoneを返す。
    ツールがレジストリから削除/リネームされていて実行不能な場合はレコードを削除しValueErrorを送出する。
    payload指定時（テナント向け）は監査ログをactor_type='tenant'・実sub/emailで記録する。"""
    pending = db.query(AgentPendingAction).filter(
        AgentPendingAction.id == pending_action_id,
        AgentPendingAction.tenant_id == tenant_id,
    ).first()
    if pending is None:
        return None
    if not (pending.expires_at > datetime.now(timezone.utc)):
        db.delete(pending)
        db.commit()
        return None

    tools = list(tools if tools is not None else _default_tools())
    tool = _find_tool(tools, pending.tool_name)
    if tool is None:
        db.delete(pending)
        db.commit()
        raise ValueError(f"Tool '{pending.tool_name}' is no longer registered")

    tool_args = _sanitize_tool_args(pending.tool_args)
    handler_kwargs = {"tenant_id": tenant_id, **tool_args}
    if payload is not None:
        handler_kwargs["payload"] = payload
    result = tool.handler(**handler_kwargs)

    if payload is not None:
        actor_type = "tenant"
        actor_id = payload.get("sub", "00000000-0000-0000-0000-000000000000")
        actor_email = payload.get("email", pending.requested_by)
    else:
        actor_type = "platform"
        actor_id = "00000000-0000-0000-0000-000000000000"
        actor_email = pending.requested_by

    write_audit_log(
        db, actor_type, actor_id, actor_email,
        f"ai_assistant_{pending.tool_name}",
        tenant_id=tenant_id, resource_type="ai_assistant_action",
        detail={"via": "ai_assistant", "confirmed_by": pending.requested_by, "tool_args": pending.tool_args},
    )
    db.delete(pending)
    db.commit()
    return result
