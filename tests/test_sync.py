"""YAML -> SQLite sync: validation stages, merge, history, write-back (PRD 8.7)."""

from resume_builder import sync as sync_mod
from resume_builder.operations import invoke
from resume_builder.security import RequestContext

from .conftest import JOB, USER, data_file


def _sync(space):
    return sync_mod.run_sync(space, RequestContext.system(USER))


def _result(report, rel):
    return next(r for r in report.results if r.rel == rel)


def test_sample_syncs_into_database(sample):
    lib = sample.repo.load_library()
    assert "fab-ci-pipeline" in lib.all_bullets()
    assert sample.repo.application_row(JOB)["stage"] == "interested"
    assert sample.repo.get_profile(JOB).gaps[0].skill == "Kubernetes"
    assert "Northwind" in sample.repo.application_row(JOB)["posting_text"]


def test_unchanged_files_are_skipped(sample):
    assert _sync(sample).results == []


def test_anchors_and_aliases_rejected(sample, roots):
    p = data_file(roots, "organizations.yaml")
    p.write_text("schema_version: 1\ncompanies: &x\n  - slug: a\n    name: A\nmore: *x\n")
    r = _result(_sync(sample), "organizations.yaml")
    assert r.result == "failed" and "anchors" in r.problems[0].message


def test_duplicate_keys_rejected(sample, roots):
    p = data_file(roots, "user.yaml")
    p.write_text(p.read_text() + "\ndisplay_name: Someone Else\n")
    r = _result(_sync(sample), "user.yaml")
    assert r.result == "failed" and "twice" in r.problems[0].message
    assert sample.user_settings.display_name == "Alex Morgan"  # last good state kept


def test_deep_nesting_rejected(sample, roots):
    p = data_file(roots, "user.yaml")
    p.write_text("a:\n" + "".join("  " * i + "- \n" for i in range(1, 20)))
    r = _result(_sync(sample), "user.yaml")
    assert r.result == "failed"


def test_control_characters_and_bad_encoding_rejected(sample, roots):
    p = data_file(roots, "user.yaml")
    p.write_bytes(b"display_name: Alex\x07\n")
    assert _result(_sync(sample), "user.yaml").result == "failed"
    p.write_bytes(b"display_name: \xff\xfe\n")
    r = _result(_sync(sample), "user.yaml")
    assert r.result == "failed" and "UTF-8" in r.problems[0].message


def test_oversized_file_rejected(sample, roots):
    p = data_file(roots, "user.yaml")
    p.write_text("# " + "x" * 1_100_000)
    assert "larger" in _result(_sync(sample), "user.yaml").problems[0].message


def test_schema_error_names_line_and_field(sample, roots):
    p = data_file(roots, "base.yaml")
    text = p.read_text().replace("status: confirmed\n  - id: sum-observability", "status: maybe\n  - id: sum-observability")
    p.write_text(text)
    r = _result(_sync(sample), "base.yaml")
    assert r.result == "failed"
    prob = r.problems[0]
    assert prob.line and prob.field and "allowed" in prob.message


def test_unknown_key_is_an_error(sample, roots):
    p = data_file(roots, "user.yaml")
    p.write_text(p.read_text().replace("headline:", "hedline:"))
    r = _result(_sync(sample), "user.yaml")
    assert r.result == "failed" and any("not a known field" in x.message for x in r.problems)


def test_failed_file_does_not_block_others(sample, roots):
    data_file(roots, "user.yaml").write_text("display_name: [broken\n")
    p = data_file(roots, "organizations.yaml")
    p.write_text(p.read_text().replace("Fictional company used in examples.", "Updated note."))
    report = _sync(sample)
    assert _result(report, "user.yaml").result == "failed"
    assert _result(report, "organizations.yaml").result == "applied"
    status = data_file(roots, "SYNC-STATUS.md").read_text()
    assert "user.yaml" in status


def test_events_are_append_only(sample, roots):
    p = data_file(roots, f"jobs/{JOB}/application.yaml")
    p.write_text(p.read_text().replace("date: 2026-11-02\n    type: stage_change", "date: 2026-11-01\n    type: stage_change"))
    r = _result(_sync(sample), f"jobs/{JOB}/application.yaml")
    assert r.result == "failed" and "correction" in r.problems[0].message


def test_new_event_appends_and_stage_transitions_checked(sample, roots):
    p = data_file(roots, f"jobs/{JOB}/application.yaml")
    p.write_text(p.read_text() + "  - date: 2026-11-04\n    type: stage_change\n    stage: accepted\n")
    r = _result(_sync(sample), f"jobs/{JOB}/application.yaml")
    assert r.result == "failed" and "can't move" in r.problems[0].message
    p.write_text(p.read_text().replace("stage: accepted", "stage: applied"))
    r = _result(_sync(sample), f"jobs/{JOB}/application.yaml")
    assert r.result == "applied", r.problems
    assert sample.repo.application_row(JOB)["stage"] == "applied"


def test_withdrawn_allowed_from_interested(sample, owner):
    out = invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {"date": "2026-11-03", "type": "stage_change", "stage": "withdrawn"}})
    assert out["stage"] == "withdrawn"


def test_write_back_keeps_comments(sample, roots, owner):
    p = data_file(roots, f"jobs/{JOB}/application.yaml")
    p.write_text("# my own comment\n" + p.read_text())
    _sync(sample)
    invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "Called"}})
    text = p.read_text()
    assert "# my own comment" in text and "notes: Called" in text


def test_three_way_merge_and_conflict(sample, roots, owner):
    p = data_file(roots, f"jobs/{JOB}/application.yaml")
    # User edits the file but hasn't synced; the app appends an event meanwhile (write-back is skipped).
    p.write_text(p.read_text().replace("interest_level: 4", "interest_level: 5"))
    out = invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note"}})
    assert out["file_updated"] is False
    r = _result(_sync(sample), f"jobs/{JOB}/application.yaml")
    assert r.result == "applied"
    app = sample.repo.get_application(JOB)
    assert app.interest_level == 5 and len(app.events) == 2  # both changes kept
    assert "type: note" in p.read_text()  # merged result written back

    # Now a real conflict: both sides change the same field.
    p.write_text(p.read_text().replace("interest_level: 5", "interest_level: 3"))
    with sample.repo.conn:
        pass
    a = sample.repo.get_application(JOB)
    a.interest_level = 1
    from resume_builder import db
    with db.transaction(sample.repo.conn):
        sample.repo.save_application(a, "interested", "owner")
    r = _result(_sync(sample), f"jobs/{JOB}/application.yaml")
    assert r.result == "conflict"
    r = sync_mod.run_sync(sample, RequestContext.system(USER), prefer={f"jobs/{JOB}/application.yaml": "file"})
    assert sample.repo.get_application(JOB).interest_level == 3


def test_removing_an_entry_archives_it(sample, roots):
    p = data_file(roots, "base.yaml")
    text = p.read_text()
    start = text.index("      - id: tail-backup")
    end = text.index("projects:")
    p.write_text(text[:start] + "\n" + text[end:])
    p2 = data_file(roots, "profiles/public.yaml")
    p2.write_text(p2.read_text().replace(", tail-backup]", "]"))
    report = _sync(sample)
    assert _result(report, "base.yaml").result == "applied"
    assert "tail-backup" in sample.repo.archived_ids()


def test_missing_reference_reports_field(sample, roots):
    p = data_file(roots, f"jobs/{JOB}/profile.yaml")
    p.write_text(p.read_text().replace("tail-docker]", "tail-docker, no-such-bullet]"))
    r = _result(_sync(sample), f"jobs/{JOB}/profile.yaml")
    assert r.result == "failed" and any("no-such-bullet" in x.message for x in r.problems)


def test_deleted_base_yaml_is_restored_from_database(sample, roots):
    from resume_builder.sync_ops import bootstrap
    data_file(roots, "base.yaml").unlink()
    bootstrap(sample.app)
    assert "fab-ci-pipeline" in data_file(roots, "base.yaml").read_text()
    assert "fab-ci-pipeline" in sample.repo.load_library().all_bullets()


def test_bootstrap_is_idempotent(sample, roots):
    from resume_builder.sync_ops import bootstrap
    before = {p: p.read_bytes() for p in (roots / "data" / USER).rglob("*.yaml")}
    report = bootstrap(sample.app)
    assert report["users"][USER]["created"] == []
    after = {p: p.read_bytes() for p in (roots / "data" / USER).rglob("*.yaml")}
    assert before == after


def test_new_optional_field_after_upgrade_is_not_a_conflict(sample, roots):
    """A merge base saved before a field existed must not look like an app-side change."""
    import json
    row = sample.repo.conn.execute("SELECT content FROM sync_files WHERE path = 'base.yaml'").fetchone()
    old = json.loads(row["content"])
    for role in old["roles"]:
        if role.get("description") is None:
            role.pop("description", None)  # as saved by an app version without role descriptions
    sample.repo.conn.execute("UPDATE sync_files SET content = ? WHERE path = 'base.yaml'", (json.dumps(old),))
    sample.repo.conn.commit()
    path = data_file(roots, "base.yaml")
    path.write_text(path.read_text(encoding="utf-8").replace("description: null", "description: Tailspin makes toys."), encoding="utf-8")
    assert _result(_sync(sample), "base.yaml").result == "applied"
    assert sample.repo.load_library().roles[1].description == "Tailspin makes toys."
