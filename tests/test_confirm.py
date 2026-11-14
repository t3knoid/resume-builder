"""Confirming library items, and the CLI's switch away from root."""

import os

import pytest

from resume_builder import cli
from resume_builder.errors import NotFound
from resume_builder.operations import invoke


def _confirm_error(app, owner, item_id: str) -> str:
    with pytest.raises(NotFound) as exc:
        invoke(app, owner, "item_confirm", {"id": item_id})
    return exc.value.message


def test_confirming_a_role_explains_that_its_bullets_are_confirmed(app, sample, owner):
    msg = _confirm_error(app, owner, "role-fabrikam")
    assert "`role-fabrikam` is a role" in msg
    assert "bullets" in msg


def test_confirming_a_role_lists_its_draft_bullets(app, sample, owner):
    invoke(app, owner, "bullet_propose", {"parent_id": "role-fabrikam", "bullet": {"id": "fab-new-draft", "text": "Draft bullet."}})
    assert "Draft bullets to confirm: fab-new-draft." in _confirm_error(app, owner, "role-fabrikam")


def test_confirming_a_project_or_skill_group_explains_why_not(app, sample, owner):
    assert "`proj-llm-lab` is a project" in _confirm_error(app, owner, "proj-llm-lab")
    assert "skill group" in _confirm_error(app, owner, "sk-automation")


def test_confirming_an_unknown_id_says_what_can_be_confirmed(app, sample, owner):
    assert "Only bullets, summary phrases, education and certifications" in _confirm_error(app, owner, "no-such-item")


@pytest.mark.parametrize("euid, puid", [(1000, "1000"), (0, ""), (0, "0"), (0, "abc")])
def test_drop_root_does_nothing_unless_root_with_a_host_user(monkeypatch, euid, puid):
    monkeypatch.setattr(os, "geteuid", lambda: euid, raising=False)
    monkeypatch.setenv("PUID", puid)
    monkeypatch.setenv("PGID", "1000")
    monkeypatch.setattr(os, "execvp", lambda *a: pytest.fail("should not re-exec"))
    cli.drop_root()


def test_drop_root_reruns_rb_as_the_host_user(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setenv("PUID", "1001")
    monkeypatch.setenv("PGID", "1002")
    monkeypatch.setattr(cli.sys, "argv", ["rb", "confirm", "fab-ci-pipeline"])
    monkeypatch.setattr(os, "execvp", lambda file, args: calls.append((file, args)))
    cli.drop_root()
    file, args = calls[0]
    assert file == "setpriv"
    assert args[1:4] == ["--reuid=1001", "--regid=1002", "--clear-groups"]
    assert args[-4:] == ["-m", "resume_builder.cli", "confirm", "fab-ci-pipeline"]


# ---------------------------------------------------------------- confirm --all

def _propose_drafts(app, owner, *ids):
    for i in ids:
        invoke(app, owner, "bullet_propose", {"parent_id": "role-fabrikam", "bullet": {"id": i, "text": f"Draft {i}."}})


def _status(app, owner, item_id):
    lib = invoke(app, owner, "library_get", {})
    return next(b["status"] for r in lib["roles"] for b in r["bullets"] if b["id"] == item_id)


def test_confirm_all_dry_run_lists_drafts_and_changes_nothing(app, sample, owner):
    _propose_drafts(app, owner, "fab-draft-a", "fab-draft-b")
    out = invoke(app, owner, "library_confirm_all", {})
    assert out["applied"] is False
    assert {"fab-draft-a", "fab-draft-b"} <= {i["id"] for i in out["items"]}
    assert _status(app, owner, "fab-draft-a") == "draft"


def test_confirm_all_applies_only_the_ids_that_were_shown(app, sample, owner):
    _propose_drafts(app, owner, "fab-draft-a", "fab-draft-b")
    out = invoke(app, owner, "library_confirm_all", {"dry_run": False, "ids": ["fab-draft-a"]})
    assert out["applied"] and out["count"] == 1
    assert _status(app, owner, "fab-draft-a") == "confirmed"
    assert _status(app, owner, "fab-draft-b") == "draft"


def test_agent_cannot_confirm_all(app, sample, agent):
    from resume_builder.errors import PermissionDenied
    with pytest.raises(PermissionDenied):
        invoke(app, agent, "library_confirm_all", {"dry_run": False})


def _cli_op(app, owner):
    return lambda name, payload=None: invoke(app, owner, name, payload or {})


def test_cli_confirm_all_asks_and_does_nothing_unless_told_yes(app, sample, owner, monkeypatch, capsys):
    _propose_drafts(app, owner, "fab-draft-a")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert cli.confirm_all(_cli_op(app, owner), yes=False, ask=lambda _: "n") == 1
    assert "Nothing changed." in capsys.readouterr().out
    assert _status(app, owner, "fab-draft-a") == "draft"
    assert cli.confirm_all(_cli_op(app, owner), yes=False, ask=lambda _: "yes") == 0
    assert _status(app, owner, "fab-draft-a") == "confirmed"


def test_cli_confirm_all_without_a_terminal_needs_yes(app, sample, owner, monkeypatch):
    _propose_drafts(app, owner, "fab-draft-a")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(cli.AppError):
        cli.confirm_all(_cli_op(app, owner), yes=False)
    assert _status(app, owner, "fab-draft-a") == "draft"
    assert cli.confirm_all(_cli_op(app, owner), yes=True) == 0
    assert _status(app, owner, "fab-draft-a") == "confirmed"
