from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    All configuration is read from environment variables.
    Defaults are safe for local development.
    """
    port: int = 8000
    processor_delay_seconds: float = 2.0
    ttl_hours: int = 24
    poll_interval_seconds: float = 0.1
    poll_timeout_seconds: float = 30.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()