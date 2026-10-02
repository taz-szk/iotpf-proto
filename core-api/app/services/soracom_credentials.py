"""テナット単位のSORACOM認証情報(Auth Key)の保存・取得。保存時に暗号化し、APIレスポンスには
Auth Key本体を絶対に含めない(設定済みかどうかとauth_key_idの末尾だけを返す)。"""
from sqlalchemy import text

from app.database import engine
from app.services.crypto import decrypt_secret, encrypt_secret


def get_credentials(tenant_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT coverage, auth_key_id, auth_key_enc FROM tenant_soracom_credentials WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).fetchone()
    if not row:
        return None
    return {
        "coverage": row.coverage,
        "auth_key_id": row.auth_key_id,
        "auth_key": decrypt_secret(row.auth_key_enc),
    }


def is_configured(tenant_id: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM tenant_soracom_credentials WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).fetchone()
    return row is not None


def get_credentials_summary(tenant_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT coverage, auth_key_id FROM tenant_soracom_credentials WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).fetchone()
    if not row:
        return {"configured": False, "coverage": None, "auth_key_id_hint": None}
    return {"configured": True, "coverage": row.coverage, "auth_key_id_hint": "..." + row.auth_key_id[-4:]}


def save_credentials(tenant_id: str, coverage: str, auth_key_id: str | None, auth_key: str | None) -> None:
    if (auth_key_id is None) != (auth_key is None):
        raise ValueError("auth_key_id and auth_key must be provided together")

    with engine.begin() as conn:
        if auth_key_id is None:
            # coverageだけの更新。既存の認証情報が無ければ対象行が無いのでno-opになる
            conn.execute(
                text("UPDATE tenant_soracom_credentials SET coverage = :coverage, updated_at = NOW() WHERE tenant_id = :tid"),
                {"tid": tenant_id, "coverage": coverage},
            )
        else:
            conn.execute(
                text("""
                    INSERT INTO tenant_soracom_credentials (tenant_id, coverage, auth_key_id, auth_key_enc)
                    VALUES (:tid, :coverage, :auth_key_id, :auth_key_enc)
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        coverage = EXCLUDED.coverage,
                        auth_key_id = EXCLUDED.auth_key_id,
                        auth_key_enc = EXCLUDED.auth_key_enc,
                        updated_at = NOW()
                """),
                {
                    "tid": tenant_id, "coverage": coverage,
                    "auth_key_id": auth_key_id, "auth_key_enc": encrypt_secret(auth_key),
                },
            )


def delete_credentials(tenant_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tenant_soracom_credentials WHERE tenant_id = :tid"), {"tid": tenant_id})
