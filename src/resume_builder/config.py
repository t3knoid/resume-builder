"""Settings that come from the environment (PRD R1.7, DK9).

Nothing here is specific to a user. Per-user settings live in that user's user.yaml.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
# Templates and migrations ship inside the package, so an installed copy (a pip or git dependency)
# works without a source checkout. RB_APP_ROOT can point elsewhere for development.
_DEFAULT_APP_ROOT = _PACKAGE_DIR


@dataclass(frozen=True)
class Settings:
    app_root: Path
    data_root: Path
    db_root: Path
    output_root: Path
    backup_root: Path
    default_user: str | None
    port: int
    sync_poll_seconds: float
    image_digest: str
    log_level: str

    @property
    def templates_dir(self) -> Path:
        return self.app_root / "templates"

    @property
    def seed_dir(self) -> Path:
        return self.templates_dir / "seed"

    @property
    def seed_version(self) -> str | None:
        f = self.seed_dir / "VERSION"
        return f.read_text(encoding="utf-8").strip() if f.exists() else None

    @property
    def migrations_dir(self) -> Path:
        return self.app_root / "migrations"

    def user_data(self, user: str) -> Path:
        return self.data_root / user

    def user_db(self, user: str) -> Path:
        return self.db_root / user / "app.db"

    def user_output(self, user: str) -> Path:
        return self.output_root / user

    def user_backups(self, user: str) -> Path:
        return self.backup_root / user


def load_settings() -> Settings:
    env = os.environ
    return Settings(
        app_root=Path(env.get("RB_APP_ROOT", _DEFAULT_APP_ROOT)),
        data_root=Path(env.get("RB_DATA_ROOT", "/data")),
        db_root=Path(env.get("RB_DB_ROOT", "/db")),
        output_root=Path(env.get("RB_OUTPUT_ROOT", "/output")),
        backup_root=Path(env.get("RB_BACKUP_ROOT", "/backups")),
        default_user=env.get("RB_DEFAULT_USER") or None,
        port=int(env.get("RB_PORT", "8000")),
        sync_poll_seconds=float(env.get("RB_SYNC_POLL_SECONDS", "3")),
        image_digest=env.get("RB_IMAGE_DIGEST", "local-dev"),
        log_level=env.get("RB_LOG_LEVEL", "info"),
    )
