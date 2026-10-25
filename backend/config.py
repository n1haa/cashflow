"""Environment configuration; existing process variables take precedence over .env."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str | URL
    cookie_secure: bool = False
    session_days: int = 7
    app_origin: str = "http://127.0.0.1:8000"

    @classmethod
    def from_env(cls):
        return cls(
            database_url=URL.create(
                "mysql+pymysql", username=os.getenv("MYSQL_USER", "cashflow"),
                password=os.getenv("MYSQL_PASSWORD", ""),
                host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                port=int(os.getenv("MYSQL_PORT", "3306")),
                database=os.getenv("MYSQL_DATABASE", "cashflow"),
                query={"charset": "utf8mb4"},
            ),
            cookie_secure=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
            session_days=max(1, min(30, int(os.getenv("SESSION_DAYS", "7")))),
            app_origin=os.getenv("APP_ORIGIN", "http://127.0.0.1:8000").rstrip("/"),
        )
