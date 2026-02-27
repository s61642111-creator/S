import os
from dataclasses import dataclass, field
from datetime import timezone

@dataclass
class Settings:
    BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
    ALLOWED_USER_ID: int = int(os.environ.get("ALLOWED_USER_ID", "0"))

    API_ID: int = int(os.environ.get("API_ID", "0"))
    API_HASH: str = os.environ.get("API_HASH", "")
    PHONE_NUMBER: str = os.environ.get("PHONE_NUMBER", "")
    SESSION_NAME: str = "userbot_session"

    WATCHED_CHANNELS: list = None

    WEBAPP_URL: str = os.environ.get("WEBAPP_URL", "https://your-domain.com")
    MINI_APP_HOST: str = os.environ.get("MINI_APP_HOST", "0.0.0.0")
    MINI_APP_PORT: int = int(os.environ.get("MINI_APP_PORT", "8080"))

    DB_PATH: str = os.environ.get("DB_PATH", "data/quiz.db")

    DAILY_REPORT_HOUR: int = 4
    DAILY_REPORT_MINUTE: int = 0

    TZ = timezone.utc

    def __post_init__(self):
        raw = os.environ.get("WATCHED_CHANNELS", "")
        self.WATCHED_CHANNELS = [c.strip() for c in raw.split(",") if c.strip()] if raw else []

settings = Settings()
