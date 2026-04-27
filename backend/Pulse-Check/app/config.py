from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 8000
    scheduler_tick_seconds: float = 1.0  # how often the watchdog checks timers

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()