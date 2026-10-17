from sqlalchemy import text
from app.database import engine
from app.services.grafana import ensure_grafana_user_in_org

_ROLE_GRAFANA = {"admin": "Viewer", "operator": "Viewer", "viewer": "Viewer"}


def create_tenant_user_record(
    tenant_id: str, schema: str, email: str, password_hash: str,
    role: str, grafana_org_id: str | None, user_id: str | None = None,
) -> None:
    """テナントスキーマのusersテーブルへ1行挿入し、Grafana orgにも同期する。
    email重複はIntegrityErrorとして呼び出し側に伝播する(HTTPエラーへの変換は呼び出し側の責務)。
    user_idを指定すればその値をidとして明示挿入する(呼び出し側がレスポンスで同じidを
    返す必要がある場合に使う)。省略時はDBのDEFAULT(uuid_generate_v4())に委ねる。"""
    with engine.connect() as conn:
        if user_id is not None:
            conn.execute(
                text(f'''
                    INSERT INTO "{schema}".users (id, email, password_hash, role)
                    VALUES (:id, :email, :hash, :role)
                '''),
                {"id": user_id, "email": email, "hash": password_hash, "role": role},
            )
        else:
            conn.execute(
                text(f'''
                    INSERT INTO "{schema}".users (email, password_hash, role)
                    VALUES (:email, :hash, :role)
                '''),
                {"email": email, "hash": password_hash, "role": role},
            )
        if grafana_org_id:
            ensure_grafana_user_in_org(int(grafana_org_id), email, _ROLE_GRAFANA[role], tenant_id)
        conn.commit()
