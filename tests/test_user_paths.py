"""File names typed to `rb` as the host sees them: backslashes, from the repo folder, or in full.

`rb` runs in the container, where the user's folder is the root. Fictional data only.
"""

from __future__ import annotations

import pytest

from resume_builder import db
from resume_builder import sync as sync_mod
from resume_builder.errors import NotFound, ValidationFailed
from resume_builder.operations import invoke
from resume_builder.security import RequestContext
from resume_builder.sync_ops import intake_rel, user_rel

from .conftest import JOB, USER, data_file

BAD = "not a file in your folder"


@pytest.mark.parametrize("given, rel", [
    (f"jobs/{JOB}/application.yaml", f"jobs/{JOB}/application.yaml"),
    (f"jobs\\{JOB}\\application.yaml", f"jobs/{JOB}/application.yaml"),
    (f".\\data\\{USER}\\jobs\\{JOB}\\application.yaml", f"jobs/{JOB}/application.yaml"),
    (f"C:\\Users\\someone\\resume-builder\\data\\{USER}\\base.yaml", "base.yaml"),
    (f"/home/{USER}/resume-builder/data/{USER}/base.yaml", "base.yaml"),     # the last `<user>/` counts
    (f"/data/{USER}/user.yaml", "user.yaml"),                                    # the container's own path
    ("./base.yaml", "base.yaml"),
])
def test_user_rel_accepts_paths_into_the_users_folder(given, rel):
    assert user_rel(given, USER, BAD) == rel


@pytest.mark.parametrize("given", [
    "", "/etc/passwd", "C:\\Windows\\win.ini", "../other/base.yaml", f"data/{USER}/../other/base.yaml",
    "/data/someone-else/base.yaml", f"data/{USER}/",
])
def test_user_rel_refuses_anything_else(given):
    with pytest.raises(ValidationFailed, match=BAD):
        user_rel(given, USER, BAD)


def test_intake_rel():
    assert intake_rel(f".\\data\\{USER}\\intake\\resume.pdf", USER) == "intake/resume.pdf"
    assert intake_rel("resume.pdf", USER) == "intake/resume.pdf"
    assert intake_rel("intake\\resume.pdf", USER) == "intake/resume.pdf"


# ---------------------------------------------------------------- rb resolve

def make_conflict(sample, roots):
    p = data_file(roots, f"jobs/{JOB}/application.yaml")
    sync_mod.run_sync(sample, RequestContext.system(USER))
    p.write_text(p.read_text().replace("interest_level: 4", "interest_level: 3"))
    a = sample.repo.get_application(JOB)
    a.interest_level = 1
    with db.transaction(sample.repo.conn):
        sample.repo.save_application(a, "interested", "owner")
    r = sync_mod.run_sync(sample, RequestContext.system(USER))
    assert any(f.rel == f"jobs/{JOB}/application.yaml" and f.result == "conflict" for f in r.results)


@pytest.mark.parametrize("given", [
    f"jobs\\{JOB}\\application.yaml",
    f".\\data\\{USER}\\jobs\\{JOB}\\application.yaml",
])
def test_resolve_takes_a_host_path(sample, owner, roots, given):
    make_conflict(sample, roots)
    invoke(sample.app, owner, "resolve", {"file": given, "keep": "file"})
    assert sample.repo.get_application(JOB).interest_level == 3


def test_resolve_refuses_paths_outside_the_folder(sample, owner):
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "resolve", {"file": "../other-user/base.yaml", "keep": "file"})
    with pytest.raises(NotFound):
        invoke(sample.app, owner, "resolve", {"file": "jobs/2026-01-01-nobody/application.yaml", "keep": "file"})


# ---------------------------------------------------------------- rb restore

def test_restore_takes_a_host_path(sample, owner):
    name = invoke(sample.app, owner, "backup", {})["backup"].replace("\\", "/").rsplit("/", 1)[-1]
    for given in (name, f".\\backups\\{USER}\\{name}", f"C:\\x\\backups\\{USER}\\{name}", f"/backups/{USER}/{name}"):
        assert invoke(sample.app, owner, "restore", {"file": given})["restored"].endswith(name)


def test_restore_refuses_another_users_backup_folder(sample, owner):
    name = sample.backup("manual").replace("\\", "/").rsplit("/", 1)[-1]
    with pytest.raises(NotFound, match="isn't in your backups folder"):
        invoke(sample.app, owner, "restore", {"file": f".\\backups\\someone-else\\{name}"})
