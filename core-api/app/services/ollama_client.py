import httpx

from app.config import settings


def embed(text: str) -> list[float]:
    """テキストをOllamaの埋め込みモデルでベクトル化する。"""
    resp = httpx.post(
        f"{settings.ollama_url}/v1/embeddings",
        json={"model": settings.ollama_embed_model, "input": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """OllamaのOpenAI互換チャットエンドポイントを呼び、assistantメッセージ部分を返す。
    戻り値の形: {"role": "assistant", "content": str | None, "tool_calls": list[dict] | None}"""
    body = {"model": settings.ollama_chat_model, "messages": messages}
    if tools:
        body["tools"] = tools
    resp = httpx.post(
        f"{settings.ollama_url}/v1/chat/completions",
        json=body,
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]
