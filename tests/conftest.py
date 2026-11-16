"""Test fixtures. Tests only ever use the fictional example user (PRD R1.5)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_builder import services, sync_ops  # noqa: F401
from resume_builder.config import load_settings
from resume_builder.security import RequestContext
from resume_builder.space import App

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "sample-user" / "alex-morgan"
USER = "alex-morgan"
JOB = "2026-11-02-northwind-labs-platform-engineering"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    for name in ("data", "db", "output", "backups"):
        (tmp_path / name).mkdir()
    monkeypatch.delenv("RB_APP_ROOT", raising=False)
    monkeypatch.setenv("RB_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("RB_DB_ROOT", str(tmp_path / "db"))
    monkeypatch.setenv("RB_OUTPUT_ROOT", str(tmp_path / "output"))
    monkeypatch.setenv("RB_BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("RB_CONTACT_EMAIL", "alex@example.com")
    monkeypatch.delenv("RB_DEFAULT_USER", raising=False)
    return tmp_path


@pytest.fixture
def app(roots):
    a = App(load_settings())
    yield a
    a.close()


@pytest.fixture
def sample(app, roots):
    """The example user, copied to a temp folder and synced."""
    shutil.copytree(EXAMPLE, roots / "data" / USER, ignore=shutil.ignore_patterns(".schemas", "SYNC-STATUS.md"))
    from resume_builder.sync_ops import bootstrap
    report = bootstrap(app)
    assert report["users"][USER]["sync"]["ok"], report["users"][USER]["sync"]
    return app.space(USER)


@pytest.fixture
def owner():
    return RequestContext.owner(USER)


@pytest.fixture
def agent():
    return RequestContext.agent(USER)


def data_file(roots, rel: str) -> Path:
    return roots / "data" / USER / rel
