import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from app.database import SessionLocal
from app.services.auth import verify_token
from app.services.rag import answer_question, execute_pending_action
from app.services.rag_indexing import reindex_all_documents

router = APIRouter(tags=["assistant"])
_bearer = HTTPBearer(auto_error=False)


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}")


class ChatMessage(BaseModel):
    message: str


class ConfirmActionBody(BaseModel):
    pending_action_id: str


@router.post("/tenants/{tenant_id}/assistant/chat")
def chat_with_assistant(tenant_id: str, body: ChatMessage, payload: dict = Depends(_require_platform)):
    tenant_uuid = _parse_uuid(tenant_id, "tenant_id")
    with SessionLocal() as db:
        return answer_question(db, tenant_id=str(tenant_uuid), message=body.message, requested_by=payload["email"])


@router.post("/tenants/{tenant_id}/assistant/confirm-action")
def confirm_assistant_action(tenant_id: str, body: ConfirmActionBody, payload: dict = Depends(_require_platform)):
    tenant_uuid = _parse_uuid(tenant_id, "tenant_id")
    pending_uuid = _parse_uuid(body.pending_action_id, "pending_action_id")
    with SessionLocal() as db:
        try:
            result = execute_pending_action(db, tenant_id=str(tenant_uuid), pending_action_id=str(pending_uuid))
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
        except TypeError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending action not found or expired")
    return {"result": result}


@router.post("/platform/assistant/reindex")
def reindex_documents(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        try:
            count = reindex_all_documents(db)
        except RuntimeError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    return {"chunk_count": count}
