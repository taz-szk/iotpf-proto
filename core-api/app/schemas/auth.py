from pydantic import BaseModel

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class LoginOut(BaseModel):
    status: str = "ok"           # "ok" | "totp_required" | "totp_setup_required"
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    partial_token: str | None = None

class RefreshRequest(BaseModel):
    refresh_token: str
