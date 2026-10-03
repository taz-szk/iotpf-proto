"""ルーターとSORACOM呼び出しの中間層。SIM(IMSI)とデバイスの紐づけ(sim_bindings、こちら独自の情報)の
CRUDと、将来「都度SORACOM API」から「定期同期+DBキャッシュ」に切り替える際の差し替え点になる。"""
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import engine, ensure_sim_tables_to_tenant_schema
from app.services.soracom_client import get_subscriber, list_subscribers
from app.services.soracom_credentials import get_credentials


class DeviceNotFoundError(Exception):
    pass


class SimAlreadyBoundError(Exception):
    pass


class DeviceAlreadyBoundError(Exception):
    pass


class BindingNotFoundError(Exception):
    pass


class SoracomNotConfiguredError(Exception):
    pass


def _get_credentials_or_raise(tenant_id: str) -> dict:
    creds = get_credentials(tenant_id)
    if creds is None:
        raise SoracomNotConfiguredError(tenant_id)
    return creds


def _attach_binding(tenant_id: str, items: list[dict]) -> list[dict]:
    bindings = get_bindings_by_imsi(tenant_id, [i["imsi"] for i in items])
    for item in items:
        b = bindings.get(item["imsi"])
        item["bound_device_id"] = b["device_id"] if b else None
        item["bound_device_name"] = b["device_name"] if b else None
    return items


def _schema(tenant_id: str) -> str:
    return f"tenant_{tenant_id.replace('-', '_')}"


def bind_line(tenant_id: str, imsi: str, device_id: str, iccid: str | None = None) -> None:
    schema = _schema(tenant_id)
    ensure_sim_tables_to_tenant_schema(tenant_id)
    with engine.begin() as conn:
        device = conn.execute(
            text(f'SELECT 1 FROM "{schema}".devices WHERE device_id = :did'), {"did": device_id},
        ).fetchone()
        if not device:
            raise DeviceNotFoundError(device_id)
        try:
            conn.execute(
                text(f'''
                    INSERT INTO "{schema}".sim_bindings (imsi, iccid, device_id)
                    VALUES (:imsi, :iccid, :device_id)
                '''),
                {"imsi": imsi, "iccid": iccid, "device_id": device_id},
            )
        except IntegrityError as e:
            # PostgreSQL UNIQUE constraint violation detail is: "Key (column_name)=(value) already exists."
            # Check for the column name with parentheses to avoid false positives from device_id values
            # that happen to contain "imsi" as a substring.
            detail = ""
            if hasattr(e, "orig") and hasattr(e.orig, "diag") and e.orig.diag and hasattr(e.orig.diag, "message_detail"):
                detail = e.orig.diag.message_detail

            if "(imsi)=" in detail:
                raise SimAlreadyBoundError(imsi) from e
            elif "(device_id)=" in detail:
                raise DeviceAlreadyBoundError(device_id) from e
            else:
                # If detail string is malformed or unexpected, re-raise the original error
                raise


def unbind_line(tenant_id: str, imsi: str) -> None:
    schema = _schema(tenant_id)
    ensure_sim_tables_to_tenant_schema(tenant_id)
    with engine.begin() as conn:
        result = conn.execute(text(f'DELETE FROM "{schema}".sim_bindings WHERE imsi = :imsi'), {"imsi": imsi})
        if result.rowcount == 0:
            raise BindingNotFoundError(imsi)


def get_bindings_by_imsi(tenant_id: str, imsis: list[str]) -> dict[str, dict]:
    if not imsis:
        return {}
    schema = _schema(tenant_id)
    ensure_sim_tables_to_tenant_schema(tenant_id)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'''
                SELECT b.imsi, b.device_id, d.device_name
                FROM "{schema}".sim_bindings b
                LEFT JOIN "{schema}".devices d ON d.device_id = b.device_id
                WHERE b.imsi = ANY(:imsis)
            '''),
            {"imsis": imsis},
        ).fetchall()
    return {r.imsi: {"device_id": r.device_id, "device_name": r.device_name or r.device_id} for r in rows}


def list_lines(
    tenant_id: str, *, status: str | None = None, speed_class: str | None = None,
    tag_name: str | None = None, tag_value: str | None = None, bound: bool | None = None,
    cursor: str | None = None, limit: int = 20,
) -> dict:
    creds = _get_credentials_or_raise(tenant_id)
    items, next_cursor = list_subscribers(
        tenant_id, creds["coverage"], creds["auth_key_id"], creds["auth_key"],
        status_filter=status, speed_class_filter=speed_class,
        tag_name=tag_name, tag_value=tag_value, limit=limit, last_evaluated_key=cursor,
    )
    items = _attach_binding(tenant_id, items)
    if bound is True:
        items = [i for i in items if i["bound_device_id"] is not None]
    elif bound is False:
        items = [i for i in items if i["bound_device_id"] is None]
    return {"items": items, "next_cursor": next_cursor}


def get_line(tenant_id: str, imsi: str) -> dict | None:
    creds = _get_credentials_or_raise(tenant_id)
    item = get_subscriber(tenant_id, creds["coverage"], creds["auth_key_id"], creds["auth_key"], imsi)
    if item is None:
        return None
    return _attach_binding(tenant_id, [item])[0]
