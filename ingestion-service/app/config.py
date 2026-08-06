from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    postgres_dsn: str = "postgresql://iotadmin:changeme@postgres:5432/iotplatform"
    influxdb_url: str = "http://influxdb:8086"
    influxdb_admin_token: str = ""
    tenant_cache_ttl_sec: int = 300

    class Config:
        env_file = ".env"

settings = Settings()
