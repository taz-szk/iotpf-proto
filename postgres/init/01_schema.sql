-- 拡張機能
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- テナント一覧
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL UNIQUE,
    slug VARCHAR(100) NOT NULL UNIQUE,
    influxdb_org_id VARCHAR(255),
    influxdb_token TEXT,
    grafana_org_id VARCHAR(255),
    public_token VARCHAR(255) UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- プラットフォーム管理者
CREATE TABLE IF NOT EXISTS platform_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ブートストラップトークン（ゼロタッチプロビジョニング用）
CREATE TABLE IF NOT EXISTS provisioning_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    token VARCHAR(255) NOT NULL UNIQUE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    max_devices INT NOT NULL DEFAULT 100,
    registered_count INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 課金統計（月次集計）
CREATE TABLE IF NOT EXISTS tenant_usage_stats (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    period CHAR(7) NOT NULL,  -- YYYY-MM
    api_call_count BIGINT NOT NULL DEFAULT 0,
    active_devices INT NOT NULL DEFAULT 0,
    user_count INT NOT NULL DEFAULT 0,
    mqtt_msg_count BIGINT NOT NULL DEFAULT 0,
    data_volume_mb NUMERIC(12,2) NOT NULL DEFAULT 0,
    ota_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, period)
);

-- 課金統計（日次生データ）
CREATE TABLE IF NOT EXISTS tenant_usage_daily (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    api_call_count BIGINT NOT NULL DEFAULT 0,
    mqtt_msg_count BIGINT NOT NULL DEFAULT 0,
    data_volume_mb NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, date)
);

-- 監査ログ
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
    actor_id UUID,
    actor_type VARCHAR(20) NOT NULL CHECK (actor_type IN ('platform_user', 'tenant_user', 'device', 'system')),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100),
    resource_id UUID,
    result VARCHAR(20) NOT NULL CHECK (result IN ('success', 'denied', 'error')),
    ip_address INET,
    metadata JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX idx_provisioning_tokens_token ON provisioning_tokens(token) WHERE is_active = TRUE;
CREATE INDEX idx_audit_logs_tenant_id ON audit_logs(tenant_id, occurred_at DESC);
CREATE INDEX idx_tenant_usage_stats_tenant_period ON tenant_usage_stats(tenant_id, period);
