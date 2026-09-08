import uuid
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.database import engine


class GroupNotFoundError(Exception):
    pass


class GroupNameConflictError(Exception):
    pass


class DeviceNotFoundError(Exception):
    pass


class GroupInUseError(Exception):
    def __init__(self, rules: list[dict]):
        self.rules = rules
        super().__init__(f"Group is referenced by {len(rules)} alert rule(s)")


def _row_to_dict(row) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_groups(schema: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(f'''
            SELECT id, name, description, created_at
            FROM "{schema}".device_groups
            ORDER BY created_at DESC
        ''')).fetchall()
    return [_row_to_dict(r) for r in rows]


def create_group(schema: str, name: str, description: str | None) -> dict:
    group_id = str(uuid.uuid4())
    with engine.connect() as conn:
        try:
            conn.execute(text(f'''
                INSERT INTO "{schema}".device_groups (id, name, description)
                VALUES (:id, :name, :description)
            '''), {"id": group_id, "name": name, "description": description})
            conn.commit()
        except IntegrityError:
            raise GroupNameConflictError(name)
        row = conn.execute(text(f'''
            SELECT id, name, description, created_at
            FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
    return _row_to_dict(row)


def update_group(schema: str, group_id: str, name: str | None, description: str | None) -> dict:
    updates = {}
    if name is not None:
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    with engine.connect() as conn:
        existing = conn.execute(text(f'''
            SELECT id FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
        if not existing:
            raise GroupNotFoundError(group_id)
        if updates:
            set_clauses = ", ".join(f"{col} = :{col}" for col in updates)
            try:
                conn.execute(text(f'''
                    UPDATE "{schema}".device_groups SET {set_clauses} WHERE id = :id
                '''), {"id": group_id, **updates})
                conn.commit()
            except IntegrityError:
                raise GroupNameConflictError(updates.get("name", ""))
        row = conn.execute(text(f'''
            SELECT id, name, description, created_at
            FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
    return _row_to_dict(row)


def delete_group(schema: str, group_id: str) -> None:
    with engine.connect() as conn:
        existing = conn.execute(text(f'''
            SELECT id FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
        if not existing:
            raise GroupNotFoundError(group_id)
        blocking = conn.execute(text(f'''
            SELECT id, sensor_key, condition, severity
            FROM "{schema}".alert_rules
            WHERE group_id = :gid AND is_active = TRUE
        '''), {"gid": group_id}).fetchall()
        if blocking:
            raise GroupInUseError([
                {"id": str(r.id), "sensor_key": r.sensor_key, "condition": r.condition, "severity": r.severity}
                for r in blocking
            ])
        conn.execute(text(f'DELETE FROM "{schema}".device_groups WHERE id = :id'), {"id": group_id})
        conn.commit()


def assign_device_group(schema: str, device_id: str, group_id: str | None) -> str | None:
    """デバイスの所属グループを変更し、変更前の group_id を返す。"""
    with engine.connect() as conn:
        row = conn.execute(text(f'''
            SELECT group_id FROM "{schema}".devices WHERE device_id = :did
        '''), {"did": device_id}).fetchone()
        if not row:
            raise DeviceNotFoundError(device_id)
        old_group_id = str(row.group_id) if row.group_id else None
        if group_id is not None:
            exists = conn.execute(text(f'''
                SELECT id FROM "{schema}".device_groups WHERE id = :gid
            '''), {"gid": group_id}).fetchone()
            if not exists:
                raise GroupNotFoundError(group_id)
        conn.execute(text(f'''
            UPDATE "{schema}".devices SET group_id = :gid WHERE device_id = :did
        '''), {"gid": group_id, "did": device_id})
        conn.commit()
    return old_group_id


def list_group_device_ids(schema: str, group_id: str) -> list[str]:
    with engine.connect() as conn:
        group = conn.execute(text(f'''
            SELECT id FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
        if not group:
            raise GroupNotFoundError(group_id)
        rows = conn.execute(text(f'''
            SELECT device_id FROM "{schema}".devices WHERE group_id = :gid
        '''), {"gid": group_id}).fetchall()
    return [r.device_id for r in rows]
