"""Configuration loading for Fable.

Precedence: environment variables > ./fable.toml > ~/.config/fable/config.toml.
The Notion token is read from NOTION_API_KEY first so it never has to live in a file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_LOCATIONS = [
    Path("fable.toml"),
    Path.home() / ".config" / "fable" / "config.toml",
]

# Gauthier's live databases. Overridable via config for anyone else using this.
DEFAULT_TODO_DB = "9ab02aaa-b2f6-4bfd-afaf-ca4366cf5e10"
DEFAULT_TRACKER_DB = "f614c4cb-9e77-4c8c-8a05-af00eac9ff5e"
DEFAULT_TIMEZONE = "Europe/Brussels"


@dataclass
class Config:
    notion_token: str = ""
    todo_db: str = DEFAULT_TODO_DB
    tracker_db: str = DEFAULT_TRACKER_DB
    log_page: str = ""  # Notion page that receives end-of-session captures
    timezone: str = DEFAULT_TIMEZONE
    stale_days: int = 7
    source_path: Path | None = field(default=None, compare=False)


def load_config() -> Config:
    cfg = Config()
    for path in CONFIG_LOCATIONS:
        if path.is_file():
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            cfg.notion_token = data.get("notion_token", cfg.notion_token)
            cfg.todo_db = data.get("todo_db", cfg.todo_db)
            cfg.tracker_db = data.get("tracker_db", cfg.tracker_db)
            cfg.log_page = data.get("log_page", cfg.log_page)
            cfg.timezone = data.get("timezone", cfg.timezone)
            cfg.stale_days = int(data.get("stale_days", cfg.stale_days))
            cfg.source_path = path
            break

    cfg.notion_token = os.environ.get("NOTION_API_KEY", cfg.notion_token)
    cfg.todo_db = os.environ.get("FABLE_TODO_DB", cfg.todo_db)
    cfg.tracker_db = os.environ.get("FABLE_TRACKER_DB", cfg.tracker_db)
    cfg.log_page = os.environ.get("FABLE_LOG_PAGE", cfg.log_page)
    cfg.timezone = os.environ.get("FABLE_TZ", cfg.timezone)
    return cfg
