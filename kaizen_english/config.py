"""Configuration: everything comes from environment variables (or a local .env file).

The bot token is NEVER stored in the code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE per line). Real environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: str
    timezone: str
    log_level: str
    default_daily_words: int = 2      # configurable to 3 in /settings
    review_per_session: int = 5       # max due words added to the daily lesson
    review_max: int = 10              # max words in one manual /review run
    practice_size: int = 4            # exercises in "additional practice"
    notify_window_hours: int = 3      # don't send a reminder later than this after the chosen time
    admin_ids: tuple[int, ...] = ()   # Telegram ids allowed to use /stats and /feedbacks


def _parse_ids(raw: str) -> tuple[int, ...]:
    return tuple(int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit())


def load_config() -> Config:
    _load_dotenv(BASE_DIR / ".env")
    _load_dotenv(BASE_DIR.parent / ".env")
    return Config(
        admin_ids=_parse_ids(os.environ.get("KAIZEN_ADMIN_IDS", "")),
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        db_path=os.environ.get("KAIZEN_DB_PATH", str(BASE_DIR / "data" / "kaizen.db")),
        timezone=os.environ.get("KAIZEN_TIMEZONE", "Europe/Moscow"),
        log_level=os.environ.get("KAIZEN_LOG_LEVEL", "INFO").upper(),
    )
