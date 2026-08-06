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
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        '''))
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".devices (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                device_id VARCHAR(255) NOT NULL UNIQUE,
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

def migrate_add_grafana_org_id() -> None:
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS grafana_org_id VARCHAR(255)
        """))
        conn.commit()
