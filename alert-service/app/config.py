from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    postgres_dsn: str = ""
    influxdb_url: str = "http://influxdb:8086"
    influxdb_admin_token: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "alerts@iot-platform.local"
    eval_interval_sec: int = 60
    device_offline_threshold_sec: int = 180

    class Config:
        env_file = ".env"

settings = Settings()
