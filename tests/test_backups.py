"""Database backups, restore and the output store, through the FileStore interface (PRD DK6, R12.14)."""

from pathlib import Path

import pytest

from resume_builder.errors import NotFound
from resume_builder.operations import invoke

from .conftest import JOB, USER


def test_backup_and_restore_round_trip(sample, owner, agent, roots):
    made = invoke(sample.app, owner, "backup", {})["backup"]
    name = Path(made).name
    assert Path(made).parent == roots / "backups" / USER
    assert name in invoke(sample.app, owner, "backups_list", {})

    before = len(sample.repo.event_drafts())
    invoke(sample.app, agent, "event_propose", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "x"}})
    assert len(sample.repo.event_drafts()) == before + 1

    out = invoke(sample.app, owner, "restore", {"file": name})
    assert len(sample.repo.event_drafts()) == before
    assert Path(out["previous_saved_as"]).name.startswith("app-pre-restore-")
    assert Path(out["previous_saved_as"]).name in invoke(sample.app, owner, "backups_list", {})


def test_restore_refuses_unknown_or_outside_names(sample, owner):
    for bad in ("app-nope.db", "../../etc/passwd", "pre-restore-files-1.zip"):
        with pytest.raises(NotFound):
            invoke(sample.app, owner, "restore", {"file": bad})


def test_backup_retention_per_kind(sample):
    for _ in range(3):
        sample.backup("manual")
    manual = sample.backups.list("app-manual-")
    assert len(manual) == 3
    assert all(k.endswith(".db") for k in manual)


def test_clean_output_empties_the_output_store(sample, owner, roots):
    out = invoke(sample.app, owner, "render", {"job_key": JOB, "export": "html"})
    assert Path(out["file"]).is_file()
    assert Path(out["file"]).is_relative_to(roots / "output" / USER)
    deleted = invoke(sample.app, owner, "clean_output", {})["deleted_files"]
    assert deleted >= 1
    assert sample.output.list() == []
    assert not Path(out["file"]).exists()
