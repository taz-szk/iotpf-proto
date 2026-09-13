import httpx

_NUM_CTX = 4096


def embed(ollama_url: str, model: str, text: str) -> list[float]:
    """テキストをOllamaの埋め込みモデルでベクトル化する。"""
    resp = httpx.post(
        f"{ollama_url}/v1/embeddings",
        json={"model": model, "input": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def chat(ollama_url: str, model: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """OllamaのOpenAI互換チャットエンドポイントを呼び、assistantメッセージ部分を返す。
    戻り値の形: {"role": "assistant", "content": str | None, "tool_calls": list[dict] | None}"""
    body = {
        "model": model,
        "messages": messages,
        "options": {"num_ctx": _NUM_CTX},
    }
    if tools:
        body["tools"] = tools
    resp = httpx.post(
        f"{ollama_url}/v1/chat/completions",
        json=body,
        timeout=300.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]
