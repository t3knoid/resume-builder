"""The intake folder: pasted postings saved as files (PRD R13.2, R13.4, D25). Fictional data only."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from resume_builder.cli import main
from resume_builder.config import load_settings
from resume_builder.errors import NotFound, PermissionDenied, ValidationFailed
from resume_builder.operations import invoke
from resume_builder.space import App
from resume_builder.sync_ops import bootstrap

from .conftest import EXAMPLE, USER, data_file

FIXTURES = Path(__file__).parent / "fixtures" / "postings"
SEED = Path(__file__).resolve().parent.parent / "src" / "resume_builder" / "templates" / "seed"
PASTED = (FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8")


def save(roots, name: str, text: str | bytes) -> Path:
    path = data_file(roots, f"intake/{name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def preview_file(app, ctx, file, url=None):
    return invoke(app, ctx, "job_intake_preview_file", {"file": file, "url": url})


# ---------------------------------------------------------------- the seeded folder

def test_bootstrap_creates_the_folder_once_and_never_overwrites(roots, monkeypatch):
    monkeypatch.setenv("RB_DEFAULT_USER", "jamie-doe")
    app = App(load_settings())
    bootstrap(app)
    readme = roots / "data" / "jamie-doe" / "intake" / "README.md"
    assert readme.read_text(encoding="utf-8") == (SEED / "intake-README.md").read_text(encoding="utf-8")
    readme.write_text("My own notes about this folder.\n", encoding="utf-8")
    report = bootstrap(app)
    assert readme.read_text(encoding="utf-8") == "My own notes about this folder.\n"
    assert "intake/README.md" not in str(report["users"]["jamie-doe"].get("created", []))
    app.close()


def test_existing_user_gets_the_folder(sample, roots):
    import shutil

    shutil.rmtree(data_file(roots, "intake"))
    bootstrap(sample.app)
    assert data_file(roots, "intake/README.md").is_file()


def test_example_user_is_built_from_the_seed():
    assert (EXAMPLE / "intake" / "README.md").read_text(encoding="utf-8") == (SEED / "intake-README.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- intake from a file

def test_preview_and_create_from_a_file(sample, owner, roots):
    path = save(roots, "springfield-sysadmin.txt", PASTED)
    p = preview_file(sample.app, owner, "intake/springfield-sysadmin.txt")
    assert p["source_path"] == "intake/springfield-sysadmin.txt"
    assert p["source"] == "Pasted text (intake/springfield-sysadmin.txt)"
    fields = {f["name"]: f["value"] for f in p["fields"]}
    assert fields["title"] == "SYSTEMS ADMINISTRATOR III" and fields["external_id"] == "714052"
    cap = sample.repo.get_posting_capture(p["capture_id"])
    assert cap.source_kind == "file" and cap.raw_content == PASTED

    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"],
                                                    "company": "contoso-health"})
    assert path.read_text(encoding="utf-8") == PASTED          # left exactly as it was
    posting = data_file(roots, f"jobs/{out['job_id']}/posting.md").read_text(encoding="utf-8")
    assert "Pasted text (intake/springfield-sysadmin.txt), captured" in posting


def test_name_without_the_folder_and_a_link(sample, owner, roots):
    save(roots, "a.txt", PASTED)
    p = preview_file(sample.app, owner, "a.txt", url="https://jobs.springfield.example.gov/job/55021")
    assert p["source_path"] == "intake/a.txt"
    assert p["source"] == "Pasted text (intake/a.txt), from https://jobs.springfield.example.gov/job/55021"


def test_sync_status_lists_used_files_until_they_change_or_go(sample, owner, roots):
    path = save(roots, "used.txt", PASTED)
    p = preview_file(sample.app, owner, "intake/used.txt")
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "company": "contoso-health"})
    invoke(sample.app, owner, "sync", {})
    status = data_file(roots, "SYNC-STATUS.md").read_text(encoding="utf-8")
    assert "## Intake files already used" in status and f"- `intake/used.txt`: started `{out['job_id']}`" in status

    path.write_text(PASTED + "\nA different posting now.\n", encoding="utf-8")   # reused for something else
    invoke(sample.app, owner, "sync", {})
    assert "intake/used.txt" not in data_file(roots, "SYNC-STATUS.md").read_text(encoding="utf-8")

    path.unlink()
    invoke(sample.app, owner, "sync", {})
    assert "Intake files already used" not in data_file(roots, "SYNC-STATUS.md").read_text(encoding="utf-8")


def test_files_are_never_taken_in_automatically(sample, owner, roots):
    jobs = sample.repo.job_ids()
    save(roots, "dropped.txt", PASTED)
    invoke(sample.app, owner, "sync", {})
    bootstrap(sample.app)
    assert sample.repo.job_ids() == jobs
    assert sample.repo.conn.execute("SELECT COUNT(*) FROM posting_captures").fetchone()[0] == 0


# ---------------------------------------------------------------- refused

@pytest.mark.parametrize("name", ["../user.yaml", "intake/../user.yaml", "/etc/passwd", "C:/Windows/win.ini", "", "  "])
def test_only_files_inside_the_intake_folder(sample, owner, name):
    with pytest.raises((ValidationFailed, NotFound)):
        preview_file(sample.app, owner, name)


def test_a_link_out_of_the_folder_is_refused(sample, owner, roots):
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks here")
    target = data_file(roots, "user.yaml")
    link = data_file(roots, "intake/sneaky.txt")
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("can't create symlinks here")
    with pytest.raises(ValidationFailed):
        preview_file(sample.app, owner, "intake/sneaky.txt")


def test_missing_file_readme_and_folder(sample, owner, roots):
    with pytest.raises(NotFound) as e:
        preview_file(sample.app, owner, "intake/nope.txt")
    assert "intake/README.md" in e.value.message
    with pytest.raises(ValidationFailed):
        preview_file(sample.app, owner, "intake/README.md")
    data_file(roots, "intake/sub").mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValidationFailed):
        preview_file(sample.app, owner, "intake/sub")


def test_oversize_file_is_refused_clearly(sample, owner, roots):
    save(roots, "huge.txt", "x" * 1_000_001)
    with pytest.raises(ValidationFailed) as e:
        preview_file(sample.app, owner, "intake/huge.txt")
    assert "larger than" in e.value.problems[0].message and e.value.problems[0].file == "intake/huge.txt"


def test_non_utf8_file_is_refused_clearly(sample, owner, roots):
    save(roots, "latin1.txt", "Café posting text".encode("latin-1"))
    with pytest.raises(ValidationFailed) as e:
        preview_file(sample.app, owner, "intake/latin1.txt")
    assert "not valid UTF-8" in e.value.problems[0].message


def test_empty_file_is_refused(sample, owner, roots):
    save(roots, "empty.txt", "\n\n")
    with pytest.raises(ValidationFailed):
        preview_file(sample.app, owner, "intake/empty.txt")


def test_only_the_owner_reads_intake_files(sample, agent, roots):
    save(roots, "x.txt", PASTED)
    with pytest.raises(PermissionDenied):
        preview_file(sample.app, agent, "intake/x.txt")


# ---------------------------------------------------------------- rb new-job --file

class Tty(io.StringIO):
    def isatty(self):
        return True


def test_cli_file_creates_the_job_and_says_the_file_can_go(sample, roots, monkeypatch, capsys):
    save(roots, "springfield.txt", PASTED)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = main(["--user", USER, "new-job", "--file", "intake/springfield.txt", "--company", "contoso-health", "--yes"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "you can delete intake/springfield.txt now" in out
    assert data_file(roots, "intake/springfield.txt").exists()


def test_cli_file_can_still_ask_questions(sample, roots, monkeypatch, capsys):
    """Unlike --stdin, --file leaves the terminal free, so the review works as with a link."""
    save(roots, "springfield.txt", PASTED)
    answers = iter(["n",                     # don't add the posting's department to the registry
                    "title", "Systems Administrator III",
                    ""])                     # create
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("sys.stdin", Tty(""))
    code = main(["--user", USER, "new-job", "--file", "springfield.txt", "--company", "contoso-health"])
    assert code == 0, capsys.readouterr().out
    job = next(j for j in sample.repo.job_ids() if "contoso-health" in j)
    assert sample.repo.get_application(job).title == "Systems Administrator III"


def test_cli_file_and_stdin_together_are_refused(sample, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASTED))
    assert main(["--user", USER, "new-job", "--file", "intake/a.txt", "--stdin", "--yes"]) == 1
    assert "--file or --stdin" in capsys.readouterr().err
