"""Deleting a user: `rb delete-user` and the `space_delete` operation. Fictional data only."""

from __future__ import annotations

import dataclasses

import pytest

from resume_builder.cli import main
from resume_builder.errors import PermissionDenied, ValidationFailed
from resume_builder.operations import REGISTRY, invoke
from resume_builder.security import ROLE_PERMISSIONS, RequestContext

from .conftest import JOB, USER


def paths(app):
    s = app.settings
    return s.user_data(USER), s.user_db(USER), s.user_output(USER), s.user_backups(USER)


def export_something(sample, owner):
    invoke(sample.app, owner, "render", {"job_key": JOB, "export": "html"})


def test_permission_is_owner_only():
    op = REGISTRY.get("space_delete")
    assert op.permission == "space:delete" and op.writes
    assert "space:delete" in ROLE_PERMISSIONS["owner"]
    assert all("space:delete" not in perms for role, perms in ROLE_PERMISSIONS.items() if role != "owner")


def test_the_agent_and_a_viewer_cannot_delete(sample, agent):
    viewer = RequestContext(actor="viewer", roles=("viewer",), user_id=USER)
    for ctx in (agent, viewer):
        with pytest.raises(PermissionDenied):
            invoke(sample.app, ctx, "space_delete", {"confirm": USER})
    assert paths(sample.app)[0].exists()


def test_deletes_files_database_and_exports_and_keeps_backups(sample, owner):
    export_something(sample, owner)
    data, db, output, backups = paths(sample.app)
    assert data.exists() and db.exists() and any(output.rglob("*.html"))
    out = invoke(sample.app, owner, "space_delete", {"confirm": USER})
    assert out["deleted"] == USER and out["exports_deleted"] >= 1 and out["backups_deleted"] == 0
    assert not data.exists() and not db.exists() and not db.parent.exists() and not output.exists()
    final = [p.name for p in backups.iterdir() if p.name.startswith("app-before-delete-")]
    assert len(final) == 1 and out["final_backup"].endswith(final[0])
    assert USER not in sample.app.discover_users()[0]


def test_the_final_backup_records_the_deletion_and_can_be_restored(sample, owner):
    out = invoke(sample.app, owner, "space_delete", {"confirm": USER})
    import sqlite3
    conn = sqlite3.connect(out["final_backup"])
    row = conn.execute("SELECT actor, channel, target FROM audit_log WHERE operation = 'space:delete'").fetchone()
    jobs = conn.execute("SELECT job_id FROM applications").fetchall()
    conn.close()
    assert row == ("owner", "cli", USER) and (JOB,) in jobs


def test_delete_backups_too(sample, owner):
    sample.backup("manual")
    out = invoke(sample.app, owner, "space_delete", {"confirm": USER, "delete_backups": True})
    assert out["backups_deleted"] >= 1 and out["final_backup"] is None
    assert not paths(sample.app)[3].exists()


def test_nothing_is_deleted_without_the_name(sample, owner):
    with pytest.raises(ValidationFailed, match="to confirm"):
        invoke(sample.app, owner, "space_delete", {"confirm": "alex"})
    data, db, _, _ = paths(sample.app)
    assert data.exists() and db.exists()
    invoke(sample.app, owner, "job_get", {"job_id": JOB})      # still works


def test_the_default_user_is_refused(sample, owner):
    sample.app.settings = dataclasses.replace(sample.app.settings, default_user=USER)
    with pytest.raises(ValidationFailed, match="RB_DEFAULT_USER"):
        invoke(sample.app, owner, "space_delete", {"confirm": USER})
    assert paths(sample.app)[0].exists()


def test_rb_delete_user(sample, capsys):
    data, db, _, _ = paths(sample.app)
    assert main(["delete-user", USER, "--confirm", "someone-else"]) == 1
    assert "Nothing was deleted" in capsys.readouterr().out and data.exists()
    assert main(["delete-user", USER, "--confirm", USER]) == 0
    out = capsys.readouterr().out
    assert f"Deleted `{USER}`" in out and "Final backup:" in out and "docker compose restart" in out
    assert not data.exists() and not db.exists()


def test_rb_delete_user_needs_a_terminal_or_confirm(sample, monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["delete-user", USER]) != 0
    assert "--confirm" in capsys.readouterr().err
    assert paths(sample.app)[0].exists()


def test_rb_delete_user_unknown(sample, capsys):
    assert main(["delete-user", "nobody"]) != 0
    assert "No user `nobody`" in capsys.readouterr().err


def test_a_deleted_user_comes_back_from_the_final_backup(sample, owner, capsys):
    """The documented way back: restore the final backup, then recreate the files from it."""
    data = paths(sample.app)[0]
    final = invoke(sample.app, owner, "space_delete", {"confirm": USER})["final_backup"]
    name = final.replace("\\", "/").split("/")[-1]
    assert main(["--user", USER, "restore", name]) == 0
    assert main(["--user", USER, "export-files"]) == 0
    assert (data / "user.yaml").exists() and (data / "base.yaml").exists()
    assert (data / "jobs" / JOB / "application.yaml").exists()
    assert main(["--user", USER, "sync"]) == 0
    assert USER in sample.app.discover_users()[0]
    capsys.readouterr()
    assert main(["--user", USER, "show", JOB]) == 0 and JOB in capsys.readouterr().out
