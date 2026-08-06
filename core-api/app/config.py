from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    postgres_dsn: str = "postgresql://iotadmin:changeme@postgres:5432/iotplatform"
    influxdb_url: str = "http://influxdb:8086"
    influxdb_admin_token: str = ""
    influxdb_org: str = "iotplatform"
    step_ca_url: str = "https://step-ca:9000"
    step_ca_root: str = "/certs/ca/root_ca.crt"
    step_ca_provisioner: str = "iot-platform"
    step_ca_password_file: str = "/home/step/secrets/password"
    jwt_secret: str = "changeme_jwt_secret_min_32_chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    grafana_url: str = "http://grafana:3000"
    grafana_admin_user: str = "admin"
    grafana_admin_password: str = "changeme"
    minio_endpoint: str = "http://minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "changeme"
    minio_firmware_bucket: str = "firmware"
    emqx_api_url: str = "http://emqx:18083"
    emqx_api_user: str = "admin"
    emqx_api_password: str = "public"
    platform_domain: str = "localhost"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
