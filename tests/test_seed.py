"""First run, seeded files and the example user (PRD 8.10)."""

from pathlib import Path

import pytest

from resume_builder.operations import invoke
from resume_builder.security import RequestContext
from resume_builder.sync_ops import bootstrap
from resume_builder.space import App
from resume_builder.config import load_settings


def test_first_run_creates_default_user_that_syncs_cleanly(roots, monkeypatch):
    monkeypatch.setenv("RB_DEFAULT_USER", "jamie-doe")
    app = App(load_settings())
    report = bootstrap(app)
    assert report["created_user"] == "jamie-doe"
    folder = roots / "data" / "jamie-doe"
    for rel in ("user.yaml", "base.yaml", "organizations.yaml", "profiles/public.yaml", "START-HERE.md", "SYNC-STATUS.md",
                ".schemas/application.schema.json"):
        assert (folder / rel).exists(), rel
    assert report["users"]["jamie-doe"]["sync"]["ok"]
    assert "Everything is in sync" in (folder / "SYNC-STATUS.md").read_text()
    status = report["users"]["jamie-doe"]["status"]
    assert "example-bullet-patching" in status["example_entries_left"]
    # The seeded public profile renders as a preview, and refuses a final render (drafts only).
    ctx = RequestContext.owner("jamie-doe")
    assert invoke(app, ctx, "render", {"job_key": "public", "preview": True, "export": "html"})["snapshot_id"]
    with pytest.raises(Exception):
        invoke(app, ctx, "render", {"job_key": "public", "export": None})
    app.close()


def test_no_default_user_creates_nothing(roots):
    app = App(load_settings())
    report = bootstrap(app)
    assert report["users"] == {} and "init-user" in report["hint"]
    app.close()


def test_invalid_folders_reported_not_changed(roots):
    (roots / "data" / "Bad Name").mkdir()
    (roots / "data" / "no-settings").mkdir()
    app = App(load_settings())
    report = bootstrap(app)
    assert len(report["folders"]) == 2
    assert list((roots / "data" / "no-settings").iterdir()) == []
    app.close()


def test_seed_files_explain_every_top_level_field():
    seed = Path(__file__).resolve().parent.parent / "src" / "resume_builder" / "templates" / "seed" / "user.yaml"
    for line in seed.read_text().splitlines():
        if line and not line.startswith((" ", "#", "-")) and ":" in line:
            assert "#" in line or line.rstrip().endswith(":"), f"no comment: {line}"


def test_example_confirm_refused(roots, monkeypatch):
    monkeypatch.setenv("RB_DEFAULT_USER", "jamie-doe")
    app = App(load_settings())
    bootstrap(app)
    with pytest.raises(Exception):
        invoke(app, RequestContext.owner("jamie-doe"), "item_confirm", {"id": "example-bullet-patching"})
    app.close()
