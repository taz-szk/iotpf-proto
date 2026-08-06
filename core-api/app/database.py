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
        conn.commit()
