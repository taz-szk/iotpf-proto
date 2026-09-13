import httpx
from sqlalchemy.orm import Session

from app.models.public import AssistantSettings

_DEFAULT_CHAT_MODEL = "qwen2.5:3b"
_DEFAULT_EMBED_MODEL = "nomic-embed-text"


def get_assistant_settings(db: Session) -> AssistantSettings:
    return db.query(AssistantSettings).filter(AssistantSettings.id == 1).first()


def is_assistant_configured(db: Session) -> bool:
    settings = get_assistant_settings(db)
    return bool(settings and settings.ollama_url)


def update_assistant_settings(
    db: Session, ollama_url: str | None, ollama_chat_model: str | None, ollama_embed_model: str | None
) -> AssistantSettings:
    settings = get_assistant_settings(db)
    settings.ollama_url = ollama_url.strip() if ollama_url and ollama_url.strip() else None
    settings.ollama_chat_model = ollama_chat_model.strip() if ollama_chat_model and ollama_chat_model.strip() else _DEFAULT_CHAT_MODEL
    settings.ollama_embed_model = ollama_embed_model.strip() if ollama_embed_model and ollama_embed_model.strip() else _DEFAULT_EMBED_MODEL
    result = {
        "ollama_url": settings.ollama_url,
        "ollama_chat_model": settings.ollama_chat_model,
        "ollama_embed_model": settings.ollama_embed_model,
    }
    db.commit()
    return result


def test_ollama_connection(ollama_url: str) -> dict:
    """Ollamaへの疎通確認を行う。/api/tagsを叩き、成功すればモデル一覧を返す。"""
    try:
        resp = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=10.0)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return {"ok": True, "models": models}
    except Exception as e:
        return {"ok": False, "error": str(e)}
