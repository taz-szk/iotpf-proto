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
            CREATE TABLE IF NOT EXISTS "{schema}".device_groups (
                id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                name        VARCHAR(100) NOT NULL,
                description TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (name)
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
                group_id UUID REFERENCES "{schema}".device_groups(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        '''))
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".alert_rules (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                device_id VARCHAR(255),
                group_id UUID,
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
            CREATE INDEX IF NOT EXISTS idx_devices_group
            ON "{schema}".devices(group_id) WHERE group_id IS NOT NULL
        '''))
        conn.execute(text(f'''
            CREATE INDEX IF NOT EXISTS idx_alert_rules_group
            ON "{schema}".alert_rules(group_id) WHERE group_id IS NOT NULL
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


def migrate_device_groups() -> None:
    """既存テナントに device_groups テーブルと devices/alert_rules の group_id 列を追加する（べき等）。"""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM tenants WHERE status != 'deleted'")).fetchall()
    for row in rows:
        schema = f"tenant_{str(row.id).replace('-', '_')}"
        with engine.connect() as conn:
            conn.execute(text(f'''
                CREATE TABLE IF NOT EXISTS "{schema}".device_groups (
                    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    name        VARCHAR(100) NOT NULL,
                    description TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (name)
                )
            '''))
            conn.execute(text(f'''
                ALTER TABLE "{schema}".devices
                ADD COLUMN IF NOT EXISTS group_id UUID
                REFERENCES "{schema}".device_groups(id) ON DELETE SET NULL
            '''))
            conn.execute(text(f'''
                ALTER TABLE "{schema}".alert_rules
                ADD COLUMN IF NOT EXISTS group_id UUID
            '''))
            conn.execute(text(f'''
                CREATE INDEX IF NOT EXISTS idx_devices_group
                ON "{schema}".devices(group_id) WHERE group_id IS NOT NULL
            '''))
            conn.execute(text(f'''
                CREATE INDEX IF NOT EXISTS idx_alert_rules_group
                ON "{schema}".alert_rules(group_id) WHERE group_id IS NOT NULL
            '''))
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


def migrate_dashboard_panel_config_group_id() -> None:
    """dashboard_panel_configs に group_id 列を追加し、UNIQUE制約を group_id 込みに張り替える（べき等）。
    NULLS NOT DISTINCT により group_id IS NULL（テナント全体のデフォルト設定）の重複もDBレベルで防止する。"""
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE dashboard_panel_configs
            ADD COLUMN IF NOT EXISTS group_id UUID
        """))
        conn.execute(text("""
            ALTER TABLE dashboard_panel_configs
            DROP CONSTRAINT IF EXISTS dashboard_panel_configs_tenant_id_sensor_key_key
        """))
        conn.execute(text("""
            ALTER TABLE dashboard_panel_configs
            DROP CONSTRAINT IF EXISTS dashboard_panel_configs_tenant_group_sensor_key_key
        """))
        conn.execute(text("""
            ALTER TABLE dashboard_panel_configs
            ADD CONSTRAINT dashboard_panel_configs_tenant_group_sensor_key_key
            UNIQUE NULLS NOT DISTINCT (tenant_id, group_id, sensor_key)
        """))
        conn.commit()


def migrate_create_audit_logs() -> None:
    """audit_logs テーブルを作成する（べき等）。

    postgres/init/01_schema.sql が旧スキーマ（actor_email/detail/created_at 列が
    存在しない非互換な audit_logs）を作成済みの DB では、CREATE TABLE IF NOT EXISTS
    が無言でスキップされてしまうため、旧スキーマを検出したら先にDROPする。
    """
    with engine.connect() as conn:
        conn.execute(text("""
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM information_schema.tables
                         WHERE table_schema = 'public' AND table_name = 'audit_logs')
                 AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                                 WHERE table_schema = 'public' AND table_name = 'audit_logs'
                                   AND column_name = 'actor_email')
              THEN
                DROP TABLE public.audit_logs CASCADE;
              END IF;
            END $$;
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                actor_type    VARCHAR(20)  NOT NULL,
                actor_id      UUID         NOT NULL,
                actor_email   VARCHAR(255) NOT NULL,
                tenant_id     UUID         REFERENCES tenants(id) ON DELETE SET NULL,
                action        VARCHAR(100) NOT NULL,
                resource_type VARCHAR(50),
                resource_id   VARCHAR(255),
                detail        JSONB,
                ip_address    VARCHAR(45),
                result        VARCHAR(10)  NOT NULL DEFAULT 'success',
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_audit_logs_tenant_created
                ON audit_logs(tenant_id, created_at DESC)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_audit_logs_created
                ON audit_logs(created_at DESC)
        """))
        conn.commit()


def migrate_create_billing_tables() -> None:
    """billing_unit_prices・billing_invoices・billing_line_items テーブルを作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_unit_prices (
                id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                item_key       VARCHAR(50) NOT NULL,
                unit_price     NUMERIC(12,4) NOT NULL,
                effective_from DATE NOT NULL,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, item_key, effective_from)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_invoices (
                id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                target_year_month  VARCHAR(7) NOT NULL,
                status             VARCHAR(20) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'finalized', 'corrected')),
                subtotal           INTEGER NOT NULL DEFAULT 0,
                tax_amount         INTEGER NOT NULL DEFAULT 0,
                total_amount       INTEGER NOT NULL DEFAULT 0,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                finalized_at       TIMESTAMPTZ
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_line_items (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                invoice_id  UUID NOT NULL REFERENCES billing_invoices(id) ON DELETE CASCADE,
                item_key    VARCHAR(50) NOT NULL,
                quantity    INTEGER NOT NULL,
                unit_price  NUMERIC(12,4) NOT NULL,
                amount      INTEGER NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_billing_unit_prices_tenant_item
                ON billing_unit_prices(tenant_id, item_key, effective_from DESC)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_billing_invoices_tenant_month
                ON billing_invoices(tenant_id, target_year_month)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_billing_line_items_invoice
                ON billing_line_items(invoice_id)
        """))
        conn.commit()


def migrate_create_billing_default_prices() -> None:
    """billing_default_unit_prices テーブルを作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_default_unit_prices (
                item_key    VARCHAR(50) PRIMARY KEY,
                unit_price  NUMERIC(12,4) NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.commit()


def migrate_create_billing_settings() -> None:
    """billing_settings テーブル（消費税率などのグローバル課金設定、シングルトン行）を作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS billing_settings (
                id       INTEGER PRIMARY KEY DEFAULT 1,
                tax_rate NUMERIC(5,4) NOT NULL DEFAULT 0.10,
                CHECK (id = 1)
            )
        """))
        conn.execute(text("""
            INSERT INTO billing_settings (id, tax_rate)
            VALUES (1, 0.10)
            ON CONFLICT (id) DO NOTHING
        """))
        conn.commit()
