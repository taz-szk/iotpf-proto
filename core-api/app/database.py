from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_tenant_schema(tenant_id: str) -> None:
    import re
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', tenant_id.lower()):
        raise ValueError(f"Invalid tenant_id format: {tenant_id}")
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    with engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".users (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'viewer'
                    CHECK (role IN (\'admin\', \'operator\', \'viewer\')),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                totp_secret VARCHAR(64),
                totp_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        '''))
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".devices (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                device_id VARCHAR(255) NOT NULL UNIQUE,
                device_name VARCHAR(255),
                provisioning_token_id UUID,
                cert_serial VARCHAR(255),
                cert_not_after TIMESTAMPTZ,
                connection_status VARCHAR(20) NOT NULL DEFAULT 'unknown'
                    CHECK (connection_status IN (\'online\', \'offline\', \'unknown\')),
                last_seen_at TIMESTAMPTZ,
                fw_version VARCHAR(100),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        '''))
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".alert_rules (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                device_id VARCHAR(255),
                sensor_key VARCHAR(255) NOT NULL,
                condition VARCHAR(20) NOT NULL
                    CHECK (condition IN (\'above\', \'below\', \'equal\', \'device_offline\')),
                threshold NUMERIC,
                trigger_mode VARCHAR(30) NOT NULL DEFAULT \'consecutive\'
                    CHECK (trigger_mode IN (\'consecutive\', \'duration\', \'consecutive_and_duration\')),
                consecutive_count INT NOT NULL DEFAULT 3,
                duration_sec INT NOT NULL DEFAULT 60,
                severity VARCHAR(20) NOT NULL DEFAULT \'warning\'
                    CHECK (severity IN (\'info\', \'warning\', \'critical\')),
                notify_emails TEXT[] NOT NULL DEFAULT \'{{}}\'::TEXT[],
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        '''))
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".alert_events (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                rule_id UUID NOT NULL REFERENCES "{schema}".alert_rules(id) ON DELETE CASCADE,
                device_id VARCHAR(255),
                triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMPTZ,
                trigger_value NUMERIC,
                notified_at TIMESTAMPTZ
            )
        '''))
        conn.execute(text(f'''
            CREATE INDEX IF NOT EXISTS idx_alert_rules_device
            ON "{schema}".alert_rules(device_id) WHERE device_id IS NOT NULL
        '''))
        conn.execute(text(f'''
            CREATE INDEX IF NOT EXISTS idx_alert_events_rule
            ON "{schema}".alert_events(rule_id, triggered_at DESC)
        '''))
        conn.commit()

def add_firmware_tables_to_tenant_schema(tenant_id: str) -> None:
    import re
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', tenant_id.lower()):
        raise ValueError(f"Invalid tenant_id: {tenant_id}")
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    with engine.connect() as conn:
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".firmware_releases (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                version VARCHAR(100) NOT NULL,
                target_model VARCHAR(100),
                minio_key TEXT NOT NULL,
                file_size BIGINT NOT NULL,
                checksum VARCHAR(128) NOT NULL,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        '''))
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".ota_events (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                firmware_id UUID NOT NULL
                    REFERENCES "{schema}".firmware_releases(id) ON DELETE CASCADE,
                device_id VARCHAR(255) NOT NULL,
                commanded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        '''))
        conn.commit()


def migrate_add_grafana_org_id() -> None:
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS grafana_org_id VARCHAR(255)
        """))
        conn.commit()


def migrate_add_provisioning_token_id() -> None:
    """全テナントの devices テーブルに provisioning_token_id カラムを追加する。
    トークンが1つだけのテナントは、NULL デバイスをそのトークンに遡及紐付けする。"""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM tenants WHERE status = 'active'")).fetchall()
    for row in rows:
        tenant_id = str(row.id)
        schema = f"tenant_{tenant_id.replace('-', '_')}"
        with engine.connect() as conn:
            conn.execute(text(f"""
                ALTER TABLE "{schema}".devices
                ADD COLUMN IF NOT EXISTS provisioning_token_id UUID
            """))
            # アクティブトークンが1つだけなら、NULL デバイスをそのトークンに紐付ける
            token_rows = conn.execute(
                text("SELECT id FROM provisioning_tokens WHERE tenant_id = :tid AND is_active = TRUE"),
                {"tid": tenant_id},
            ).fetchall()
            if len(token_rows) == 1:
                conn.execute(text(f"""
                    UPDATE "{schema}".devices
                    SET provisioning_token_id = :tok_id
                    WHERE provisioning_token_id IS NULL
                """), {"tok_id": str(token_rows[0].id)})
            conn.commit()


def migrate_add_public_token() -> None:
    """tenants テーブルに public_token カラムを追加する。"""
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS public_token VARCHAR(255) UNIQUE
        """))
        conn.commit()


def migrate_add_token_version() -> None:
    """platform_users テーブルに token_version カラムを追加する。"""
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE platform_users
            ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 1
        """))
        conn.commit()


def migrate_add_device_name() -> None:
    """全テナントの devices テーブルに device_name カラムを追加する。"""
    from app.models.public import Tenant
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM tenants WHERE status = 'active'")).fetchall()
    for row in rows:
        schema = f"tenant_{str(row.id).replace('-', '_')}"
        with engine.connect() as conn:
            conn.execute(text(f"""
                ALTER TABLE "{schema}".devices
                ADD COLUMN IF NOT EXISTS device_name VARCHAR(255)
            """))
            conn.commit()


def migrate_totp_columns() -> None:
    """platform_users と全テナント users テーブルに TOTP 列を追加（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE platform_users
            ADD COLUMN IF NOT EXISTS totp_secret  VARCHAR(64),
            ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mfa_settings (
                id                INTEGER PRIMARY KEY DEFAULT 1,
                platform_required BOOLEAN NOT NULL DEFAULT FALSE,
                tenant_required   BOOLEAN NOT NULL DEFAULT FALSE,
                CHECK (id = 1)
            )
        """))
        conn.execute(text("""
            INSERT INTO mfa_settings (id, platform_required, tenant_required)
            VALUES (1, FALSE, FALSE)
            ON CONFLICT (id) DO NOTHING
        """))
        rows = conn.execute(text("SELECT id FROM tenants WHERE status != 'deleted'")).fetchall()
        conn.commit()
    for row in rows:
        schema = f"tenant_{str(row.id).replace('-', '_')}"
        with engine.connect() as conn:
            conn.execute(text(f"""
                ALTER TABLE "{schema}".users
                ADD COLUMN IF NOT EXISTS totp_secret  VARCHAR(64),
                ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE
            """))
            conn.commit()


def migrate_dashboard_panel_configs() -> None:
    """dashboard_panel_configs テーブルを作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dashboard_panel_configs (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                sensor_key  VARCHAR(64) NOT NULL,
                panel_type  VARCHAR(20) NOT NULL DEFAULT 'timeseries',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, sensor_key)
            )
        """))
        conn.commit()
