from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # DB — docker-compose constructs this from POSTGRES_USER/PASSWORD/DB
    postgres_dsn: str = ""
    influxdb_url: str = "http://influxdb:8086"
    influxdb_admin_token: str = ""
    influxdb_org: str = "iotplatform"
    step_ca_url: str = "https://step-ca:9000"
    step_ca_root: str = "/certs/ca/root_ca.crt"
    step_ca_provisioner: str = "iot-platform"
    step_ca_password_file: str = "/home/step/secrets/password"
    # Secrets — no default; app refuses to start if these are not set
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    grafana_url: str = "http://grafana:3000"
    grafana_admin_user: str = "admin"
    grafana_admin_password: str
    minio_endpoint: str = "http://minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str
    minio_firmware_bucket: str = "firmware"
    emqx_api_url: str = "http://emqx:18083"
    emqx_api_user: str = "admin"
    emqx_api_password: str
    # min_length: docker-compose passes "" (not unset) when EMQX_WEBHOOK_SECRET is
    # missing from .env; an empty/weak secret makes the HMAC check in
    # emqx_events.py trivially bypassable (hmac.compare_digest(b"", b"") == True).
    emqx_webhook_secret: str = Field(min_length=32)
    platform_domain: str = "localhost"
    grafana_session_expire_hours: int = 24

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
