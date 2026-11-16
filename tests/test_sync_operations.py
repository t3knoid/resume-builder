"""YAML sync applies each file through an operation, as the owner on yaml-sync (issue #5)."""

import pytest

from resume_builder import db
from resume_builder import sync as sync_mod
from resume_builder.changes import Change
from resume_builder.errors import PermissionDenied, ValidationFailed
from resume_builder.operations import invoke
from resume_builder.security import DenyAllPolicy, RequestContext

from .conftest import JOB, USER, data_file

REPLACE_OPS = ("settings_replace", "registry_replace", "library_replace", "application_replace", "posting_replace")


def _sync(space, **kw):
    return sync_mod.run_sync(space, RequestContext.system(USER), **kw)


def _result(report, rel):
    return next(r for r in report.results if r.rel == rel)


def _audit(space):
    return [dict(r) for r in space.repo.audit_entries(500)]


def _edit_interest(roots):
    p = data_file(roots, f"jobs/{JOB}/application.yaml")
    p.write_text(p.read_text().replace("interest_level: 4", "interest_level: 5"))


def test_each_file_is_an_audited_operation_as_the_owner(sample, roots):
    _edit_interest(roots)
    p = data_file(roots, "base.yaml")
    p.write_text("# a comment\n" + p.read_text())
    p = data_file(roots, f"jobs/{JOB}/posting.md")
    p.write_text(p.read_text() + "\nAlso: on-call one week in six.\n")
    report = _sync(sample)
    assert all(r.result == "applied" for r in report.results), [(r.rel, r.problems) for r in report.results]
    entries = [e for e in _audit(sample) if e["channel"] == "yaml-sync"]
    ops = {e["operation"] for e in entries}
    assert {"application_replace", "library_replace", "posting_replace"} <= ops
    assert all(e["actor"] == "owner" and e["result"] == "ok" for e in entries)
    assert sample.repo.get_application(JOB).interest_level == 5


def test_sync_goes_through_the_policy(sample, roots):
    """A file edit gets the same permission check as the CLI and API."""
    sample.app.policy = DenyAllPolicy()
    _edit_interest(roots)
    r = _result(_sync(sample), f"jobs/{JOB}/application.yaml")
    assert r.result == "failed" and "not allowed" in r.problems[0].message
    assert sample.repo.get_application(JOB).interest_level == 4  # last good state kept


def test_check_only_is_a_dry_run_through_the_operation(sample, roots):
    before = len(_audit(sample))
    _edit_interest(roots)
    heard = []
    sample.app.change_listeners.append(lambda space, change: heard.append(change) or False)
    r = _result(_sync(sample, check_only=True), f"jobs/{JOB}/application.yaml")
    assert r.result == "applied" and "Would be applied" in r.problems[0].message
    assert sample.repo.get_application(JOB).interest_level == 4
    assert len(_audit(sample)) == before  # rolled back with the rest
    assert heard == []  # no listener hears of a change that didn't happen


def test_other_changes_are_sent_after_commit(sample, roots, owner):
    """profile_write marks an approved letter out of date; that change reaches the listeners once committed."""
    invoke(sample.app, owner, "document_propose", {"job_id": JOB, "kind": "answer", "text": "I like the platform team's on-call model."})
    invoke(sample.app, owner, "document_approve", {"job_id": JOB, "kind": "answer"})
    heard = []
    sample.app.change_listeners.append(lambda space, change: heard.append(change) or False)
    p = data_file(roots, f"jobs/{JOB}/profile.yaml")
    p.write_text("# reordered by hand\n" + p.read_text())
    r = _result(_sync(sample), f"jobs/{JOB}/profile.yaml")
    assert r.result == "applied", r.problems
    assert Change("document", JOB, "answer", "why-here") in heard
    assert Change("profile", JOB) not in heard  # the synced file itself is recorded by the adapter


def test_application_replace_keeps_events_append_only(sample, owner):
    app = sample.repo.get_application(JOB).model_dump(mode="json")
    app["events"][0]["date"] = "2026-10-01"
    with pytest.raises(ValidationFailed) as exc:
        invoke(sample.app, owner, "application_replace", {"application": app})
    assert "correction" in exc.value.problems[0].message


def test_library_replace_archives_what_is_left_out(sample, owner):
    lib = sample.repo.load_library().model_dump(mode="json")
    removed = lib["roles"][0]["bullets"].pop()["id"]
    invoke(sample.app, owner, "library_replace", {"library": lib})
    assert removed not in sample.repo.load_library().all_bullets()
    assert removed in sample.repo.load_library(include_archived=True).all_bullets()


@pytest.mark.parametrize("name", REPLACE_OPS)
def test_agent_and_viewer_cannot_replace(sample, agent, name):
    viewer = RequestContext(actor="viewer", roles=("viewer",), user_id=USER)
    for ctx in (agent, viewer):
        with pytest.raises(PermissionDenied):
            invoke(sample.app, ctx, name, {})


def test_nested_transactions_are_savepoints(sample):
    conn = sample.repo.conn
    conn.execute("CREATE TABLE IF NOT EXISTS t_nested (x INTEGER)")
    with db.transaction(conn):
        conn.execute("INSERT INTO t_nested VALUES (1)")
        with pytest.raises(RuntimeError):
            with db.transaction(conn):
                conn.execute("INSERT INTO t_nested VALUES (2)")
                raise RuntimeError
    assert [r[0] for r in conn.execute("SELECT x FROM t_nested")] == [1]  # only the inner part rolled back
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            with db.transaction(conn):
                conn.execute("INSERT INTO t_nested VALUES (3)")
            raise RuntimeError
    assert [r[0] for r in conn.execute("SELECT x FROM t_nested")] == [1]  # the outer rollback undoes the inner part
