"""ルーターとSORACOM呼び出しの中間層。SIM(IMSI)とデバイスの紐づけ(sim_bindings、こちら独自の情報)の
CRUDと、将来「都度SORACOM API」から「定期同期+DBキャッシュ」に切り替える際の差し替え点になる。"""
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import engine, ensure_sim_tables_to_tenant_schema


class DeviceNotFoundError(Exception):
    pass


class SimAlreadyBoundError(Exception):
    pass


class DeviceAlreadyBoundError(Exception):
    pass


class BindingNotFoundError(Exception):
    pass


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
            detail = str(getattr(getattr(e, "orig", None), "diag", None) and e.orig.diag.message_detail or e)
            if "imsi" in detail:
                raise SimAlreadyBoundError(imsi) from e
            raise DeviceAlreadyBoundError(device_id) from e


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
