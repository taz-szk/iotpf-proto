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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
