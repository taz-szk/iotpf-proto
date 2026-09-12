import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.rag import AgentPendingAction
from app.services.audit import write_audit_log
from app.services.ollama_client import chat, embed
from app.services.rag_tools import TOOLS

_MAX_TOOL_ROUNDS = 2
_TOP_K_CHUNKS = 5
_PENDING_ACTION_TTL_MINUTES = 10

_SYSTEM_PROMPT = (
    "あなたはIoTプラットフォームの操作・仕様に関する質問に答えるアシスタントです。"
    "提供されたドキュメントの抜粋を参考に、日本語で簡潔に回答してください。"
    "プラットフォームの現在の情報が必要な場合はツールを使ってください。"
    "ユーザーが操作の実行（トークン発行・設定変更・アラート作成等）を依頼した場合は、"
    "該当するツールを呼び出してください（実際の実行はユーザーの確認後に行われます）。"
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


def answer_question(db: Session, tenant_id: str, message: str, requested_by: str) -> dict:
    """質問に回答する。読み取り専用ツールは即実行し、アクション実行ツールは
    pending_actionとして保存し確認待ちにする。
    戻り値: {"answer": str, "sources": list[dict], "pending_action": dict | None}"""
    tools = list(TOOLS)
    question_embedding = embed(message)
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
        response = chat(messages=messages, tools=_tool_schemas(tools))

        if not response.get("tool_calls"):
            return {"answer": response.get("content") or "", "sources": sources, "pending_action": None}

        tool_call = response["tool_calls"][0]
        tool_name = tool_call["function"]["name"]
        tool_args = json.loads(tool_call["function"]["arguments"] or "{}")
        tool = _find_tool(tools, tool_name)

        if tool is None:
            messages.append({"role": "assistant", "content": None, "tool_calls": response["tool_calls"]})
            messages.append({"role": "tool", "content": json.dumps({"error": "unknown tool"})})
            continue

        if tool.read_only:
            result = tool.handler(tenant_id=tenant_id, **tool_args)
            messages.append({"role": "assistant", "content": None, "tool_calls": response["tool_calls"]})
            messages.append({"role": "tool", "content": json.dumps(result, default=str)})
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

    return {"answer": response.get("content") or "", "sources": sources, "pending_action": None}


def execute_pending_action(db: Session, tenant_id: str, pending_action_id: str):
    """未確認アクションを実行する。見つからない/期限切れならNoneを返す。"""
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

    tools = list(TOOLS)
    tool = _find_tool(tools, pending.tool_name)
    result = tool.handler(tenant_id=tenant_id, **pending.tool_args)

    write_audit_log(
        db, "platform", "00000000-0000-0000-0000-000000000000", pending.requested_by,
        f"ai_assistant_{pending.tool_name}",
        tenant_id=tenant_id, resource_type="ai_assistant_action",
        detail={"via": "ai_assistant", "confirmed_by": pending.requested_by, "tool_args": pending.tool_args},
    )
    db.delete(pending)
    db.commit()
    return result
