"""Secrets: contact details and publish credentials (PRD R5.2, R12.3, DK10).

`user.yaml` holds only the *names* of secrets. Values come from a `SecretStore`, never from user
files, the database or a snapshot. Locally that is Docker secrets (`/run/secrets/<name>`) or
environment variables. A host can pass any object with the same `get` method to `App(secrets=...)`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping, Protocol

_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class SecretStore(Protocol):
    def get(self, name: str, *, user_id: str | None = None) -> str | None:
        """The secret's value, or None if it isn't set. `user_id` lets a host keep secrets per user."""
        ...


class EnvSecretStore:
    """Docker secrets first (a file per secret), then environment variables."""

    def __init__(self, secrets_dir: str | Path | None = None, environ: Mapping[str, str] | None = None):
        self.environ = os.environ if environ is None else environ
        self.secrets_dir = Path(secrets_dir or self.environ.get("RB_SECRETS_DIR", "/run/secrets"))

    def get(self, name: str, *, user_id: str | None = None) -> str | None:
        if not name or not _NAME.match(name):
            return None  # a name with a path separator could read any file
        path = self.secrets_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8").strip() or None
        return self.environ.get(name) or None


class DictSecretStore:
    """Fixed values, for tests and embedding."""

    def __init__(self, values: Mapping[str, str]):
        self.values = dict(values)

    def get(self, name: str, *, user_id: str | None = None) -> str | None:
        return self.values.get(name) or None
