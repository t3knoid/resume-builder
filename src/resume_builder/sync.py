"""YAML/Markdown -> SQLite sync, and write-back of app changes (PRD section 8.7).

The database is the source of truth. User files are input. For each changed file, in dependency
order (R7.8): safe read -> schema -> content rules -> references -> history -> three-way merge
-> database dry run in a transaction -> commit. A file is applied whole or not at all, and a
failed file keeps its last good state in the database.

Applying a file is an operation call through `invoke()`, as the owner on the `yaml-sync` channel
(issue #5): `settings_replace`, `registry_replace`, `library_replace`, `profile_write`,
`application_replace`, `posting_replace` and `document_propose`. So file edits get the same
permission check, validation and audit as the CLI and API. The call runs inside this adapter's
transaction (the operation's own transaction becomes a savepoint), which a dry run rolls back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from . import db, yamlio
from .changes import Change, hold_changes
from .errors import AppError, Problem
from .layout import doc_rel
from .models import (
    SCHEMA_VERSION, Application, ContentLibrary, OrganizationRegistry, Profile, UserSettings,
)
from .security import RequestContext
from .space import UserSpace, file_hash
from .validation import (
    pydantic_problems, validate_application, validate_library, validate_profile, validate_registry,
)

_MISSING = object()

DOC_FILES = {"fit-report.md": ("fit_report", ""), "cover-letter.md": ("cover_letter", "")}
# A profile's mappings whose key order is data: the order shown on the resume.
PROFILE_ORDERED = ("roles", "projects", "earlier_roles")
DEFAULT_QUESTIONS = {"why-here": "Why do you want to work here?"}


class _DryRun(Exception):
    pass


@dataclass
class FileResult:
    rel: str
    result: str  # applied | unchanged | failed | conflict | waiting | missing
    problems: list[Problem] = field(default_factory=list)


@dataclass
class SyncReport:
    results: list[FileResult] = field(default_factory=list)
    backup: str | None = None

    def by(self, result: str) -> list[FileResult]:
        return [r for r in self.results if r.result == result]

    @property
    def ok(self) -> bool:
        return not any(r.result in ("failed", "conflict", "waiting") for r in self.results)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "backup": self.backup,
            "files": [
                {"file": r.rel, "result": r.result, "problems": [p.to_dict() for p in r.problems]}
                for r in self.results
            ],
        }


# ---------------------------------------------------------------- three-way merge

def three_way(base: Any, theirs: Any, ours: Any, path: tuple = ()) -> tuple[Any, list[tuple]]:
    """Merge the user's file edits (theirs) with app changes (ours) since the last sync (base)."""
    if ours is _MISSING or ours is None:
        return theirs, []  # nothing in the database yet: the file wins
    if base is None or base is _MISSING:
        # No common ancestor (first sync of this file, or a key only one side has).
        return (ours if theirs is _MISSING else theirs), []
    if theirs == base:
        return ours, []
    if ours == base or theirs == ours:
        return theirs, []
    if isinstance(base, dict) and isinstance(theirs, dict) and isinstance(ours, dict):
        merged, conflicts = {}, []
        for key in list(dict.fromkeys([*ours.keys(), *theirs.keys()])):
            m, c = three_way(base.get(key, _MISSING), theirs.get(key, _MISSING), ours.get(key, _MISSING), path + (key,))
            if m is not _MISSING:
                merged[key] = m
            conflicts += c
        return merged, conflicts
    return ours, [path]


# ---------------------------------------------------------------- exports (database -> plain data)

def export_settings(space: UserSpace) -> dict | None:
    s = space.repo.get_settings()
    return s.model_dump(mode="json") if s else None


def export_library(space: UserSpace) -> dict | None:
    if not space.repo.has_library():
        return None
    return space.repo.load_library().model_dump(mode="json")


def export_registry(space: UserSpace) -> dict | None:
    if not space.repo.has_registry():
        return None
    return space.repo.load_registry().model_dump(mode="json")


def export_profile(space: UserSpace, key: str) -> dict | None:
    p = space.repo.get_profile(key)
    return p.model_dump(mode="json") if p else None


def export_application(space: UserSpace, job_id: str) -> dict | None:
    if not space.repo.application_row(job_id):
        return None
    return space.repo.get_application(job_id).model_dump(mode="json")


# ---------------------------------------------------------------- write-back (R7.12)

def doc_header(job_id: str, kind: str, slug: str, status: str) -> str:
    label = {"fit_report": "fit report", "cover_letter": "cover letter", "answer": f"answer ({slug})"}[kind]
    how = f"rb approve {job_id} {kind}" + (f" --slug {slug}" if slug else "")
    cite = " Claims need a [cite: bullet-id] marker at the end of the sentence." if kind != "fit_report" else ""
    approve = f" Approve with: {how}" if kind != "fit_report" else ""
    return f"<!-- resume-builder: {label} for {job_id}. Status: {status}.{approve}.{cite} Edits here sync back. -->\n\n"


def write_back_all(space: UserSpace, what: str, key: str | None = None) -> bool:
    """Write the database's current state for one item back to its file."""
    if what == "settings":
        data = export_settings(space)
        return bool(data) and space.write_back_yaml("user.yaml", data, "user.yaml")
    if what == "library":
        data = export_library(space)
        return bool(data) and space.write_back_yaml("base.yaml", data, "base.yaml")
    if what == "registry":
        data = export_registry(space) or {"schema_version": SCHEMA_VERSION, "companies": []}
        return space.write_back_yaml("organizations.yaml", data, "organizations.yaml")
    if what == "profile":
        data = export_profile(space, key)
        rel = "profiles/public.yaml" if key == "public" else f"jobs/{key}/profile.yaml"
        return bool(data) and space.write_back_yaml(rel, data, "profile.yaml", ordered=PROFILE_ORDERED)
    if what == "application":
        data = export_application(space, key)
        if not data:
            return False
        return space.write_back_yaml(f"jobs/{key}/application.yaml", data, "application.yaml", file_plain=tidy_application(data))
    if what == "posting":
        row = space.repo.application_row(key)
        text = (row["posting_text"] if row else None) or ""
        if not text:
            return False
        return space.write_back_text(f"jobs/{key}/posting.md", text, text)
    raise ValueError(what)


def tidy_application(data: dict) -> dict:
    """Leave empty optional fields out of events in the file; the database keeps the full record."""
    out = dict(data)
    out["events"] = [{k: v for k, v in ev.items() if v not in (None, [], {})} for ev in data.get("events", [])]
    return out


def write_back_document(space: UserSpace, job_id: str, kind: str, slug: str = "") -> bool:
    row = space.repo.get_document(job_id, kind, slug)
    if not row:
        return False
    text = row["text"]
    return space.write_back_text(doc_rel(job_id, kind, slug), doc_header(job_id, kind, slug, row["status"]) + text, text)


# ---------------------------------------------------------------- the sync run

def plan(space: UserSpace) -> list[str]:
    """Input files in dependency order (R7.8)."""
    files = ["user.yaml", "organizations.yaml", "base.yaml", "profiles/public.yaml"]
    jobs_dir = space.data_dir / "jobs"
    if jobs_dir.exists():
        for job in sorted(p for p in jobs_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
            for name in ("application.yaml", "posting.md", "profile.yaml", "fit-report.md", "cover-letter.md"):
                files.append(f"jobs/{job.name}/{name}")
            answers = job / "answers"
            if answers.exists():
                files += [f"jobs/{job.name}/answers/{a.name}" for a in sorted(answers.glob("*.md"))]
    return files


def run_sync(space: UserSpace, ctx: RequestContext, check_only: bool = False, prefer: dict[str, str] | None = None) -> SyncReport:
    """Sync changed input files into the database. `prefer` maps file -> 'file' | 'app' to resolve conflicts."""
    prefer = prefer or {}
    # The files are the owner's own input, whoever started the sync (bootstrap, the watcher, the
    # CLI), so each file is applied as the owner on the yaml-sync channel (issue #5).
    ctx = replace(ctx, actor="owner", roles=("owner",), user_id=space.user, channel="yaml-sync")
    report = SyncReport()
    with db.file_lock(space.lock_path):
        run_id = None if check_only else space.repo.start_sync_run()
        failed_files: set[str] = set()
        backed_up = False

        def ensure_backup():
            nonlocal backed_up
            if not backed_up and not check_only:
                report.backup = str(space.backup("pre-change"))
                backed_up = True

        planned = plan(space)
        for rel in planned:
            p = space.data_dir / rel
            if not p.exists():
                continue
            row = space.repo.sync_file(rel)
            h = file_hash(p)
            if rel in prefer and prefer[rel] == "app":
                _resolve_keep_app(space, rel)
                report.results.append(FileResult(rel, "applied", [Problem("Kept the app's version and rewrote the file.", file=rel, severity="warning")]))
                continue
            if row and row["hash"] == h and row["result"] in ("applied", "unchanged") and rel not in prefer:
                continue
            res = _sync_file(space, ctx, rel, p, row, check_only, ensure_backup, failed_files, prefer.get(rel) == "file")
            report.results.append(res)
            if res.result in ("failed", "conflict", "waiting"):
                failed_files.add(rel)
                if not check_only:
                    space.repo.mark_sync_problem(rel, res.result, [x.to_dict() for x in res.problems])

        # Files that were synced before but are gone now: report, never delete data (R7.10).
        for row in space.repo.sync_files():
            if row["path"] not in planned or not (space.data_dir / row["path"]).exists():
                if row["path"].startswith("jobs/") and row["path"].endswith("application.yaml"):
                    job = row["path"].split("/")[1]
                    if space.repo.application_row(job) and not space.repo.application_row(job)["archived"]:
                        report.results.append(FileResult(row["path"], "missing", [Problem(
                            f"The folder for `{job}` is missing. Nothing was deleted. Run `rb export-files` to recreate it, "
                            f"or `rb archive-job {job}` to archive the application.", file=row["path"], severity="warning")]))

        if not check_only:
            applied = len(report.by("applied"))
            failed = len([r for r in report.results if r.result in ("failed", "conflict", "waiting")])
            space.repo.finish_sync_run(run_id, applied, failed, report.to_dict())
            write_status_file(space, report)
            if any(r.rel in ("organizations.yaml",) and r.result == "applied" for r in report.results):
                from .sync_ops import write_schemas
                write_schemas(space)
    return report


def _sync_file(space, ctx, rel, path, row, check_only, ensure_backup, failed_files, force_file) -> FileResult:
    try:
        if rel.endswith(".yaml"):
            return _sync_yaml(space, ctx, rel, path, row, check_only, ensure_backup, failed_files, force_file)
        return _sync_markdown(space, ctx, rel, path, row, check_only, ensure_backup, force_file)
    except AppError as exc:
        return FileResult(rel, "failed", exc.problems or [Problem(exc.message, file=rel)])


def _kind(rel: str) -> tuple[str, str | None]:
    if rel == "user.yaml":
        return "settings", None
    if rel == "organizations.yaml":
        return "registry", None
    if rel == "base.yaml":
        return "library", None
    if rel == "profiles/public.yaml":
        return "profile", "public"
    parts = rel.split("/")
    if parts[0] == "jobs" and parts[-1] == "application.yaml":
        return "application", parts[1]
    if parts[0] == "jobs" and parts[-1] == "profile.yaml":
        return "profile", parts[1]
    return "unknown", None


_MODELS: dict[str, type[BaseModel]] = {
    "settings": UserSettings, "registry": OrganizationRegistry, "library": ContentLibrary,
    "profile": Profile, "application": Application,
}


def _sync_yaml(space: UserSpace, ctx, rel, path: Path, row, check_only, ensure_backup, failed_files, force_file) -> FileResult:
    kind, key = _kind(rel)
    if kind == "unknown":
        return FileResult(rel, "failed", [Problem("This file isn't one the app knows how to read.", file=rel)])
    model_cls = _MODELS[kind]

    # Stage 1: safe reading.
    doc, problems = yamlio.read_yaml(path, rel)
    if doc is None:
        return FileResult(rel, "failed", problems)
    theirs_raw = yamlio.to_plain(doc)

    # Stage 2: schema (with schema_version check).
    version = theirs_raw.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        return FileResult(rel, "failed", [Problem(
            f"schema_version {version} is newer than this app understands ({SCHEMA_VERSION}). Update the app image.",
            file=rel, field="schema_version")])
    if kind == "application" and "job_id" not in theirs_raw:
        theirs_raw["job_id"] = key
    try:
        model = model_cls.model_validate(theirs_raw)
    except ValidationError as exc:
        return FileResult(rel, "failed", pydantic_problems(exc, rel, doc, yamlio.line_of))
    theirs = model.model_dump(mode="json")
    file_plain = theirs  # what the file says, before folding in database-only events

    # Stage 3–5: content rules, references, history.
    problems = _rules(space, kind, key, model, rel)
    base = json.loads(row["content"]) if row and row["content"] else None
    if base is not None:
        # The last-synced copy may come from an older app version, without fields added since.
        # Read it through the current model so new optional fields don't look like app changes.
        try:
            base = model_cls.model_validate(base).model_dump(mode="json")
        except ValidationError:
            pass
    ours = _export(space, kind, key)
    if kind == "application":
        hist_problems, theirs, base_cmp, ours_cmp = _event_history(theirs, base, ours, rel, key)
        problems += hist_problems
    else:
        base_cmp, ours_cmp = base, ours
    errors = [p for p in problems if p.severity == "error"]
    if errors:
        waiting = [p for p in errors if "organizations.yaml" in p.message or "content library" in p.message]
        if waiting and failed_files & {"organizations.yaml", "base.yaml"}:
            return FileResult(rel, "waiting", [Problem(
                "Waiting for a fix in " + ", ".join(sorted(failed_files & {"organizations.yaml", "base.yaml"})) + ".", file=rel)] + errors)
        return FileResult(rel, "failed", problems)

    # Stage 6: three-way merge with app changes since the last sync.
    if force_file:
        merged, conflicts = theirs, []
    else:
        merged, conflicts = three_way(base_cmp, theirs, ours_cmp)
    if conflicts:
        return FileResult(rel, "conflict", [_conflict_problem(rel, c, theirs, ours_cmp) for c in conflicts])
    if kind == "application":
        merged["events"] = theirs["events"]
    try:
        merged_model = model_cls.model_validate(merged)
    except ValidationError as exc:
        return FileResult(rel, "failed", pydantic_problems(exc, rel))
    problems = _rules(space, kind, key, merged_model, rel)
    if any(p.severity == "error" for p in problems):
        return FileResult(rel, "failed", problems)

    # Stage 7: apply through the operation inside a transaction; constraints are checked by the database too.
    if not check_only:
        ensure_backup()
    op, payload = _OPERATIONS[kind](key, merged_model)
    try:
        if _run(space, ctx, op, payload, Change(kind, key), check_only) is _DRY_RUN:
            return FileResult(rel, "applied", [Problem("Would be applied.", file=rel, severity="warning")] + problems)
    except AppError as exc:
        return FileResult(rel, "failed", exc.problems or [Problem(exc.message, file=rel)])
    except Exception as exc:  # sqlite3.IntegrityError and friends
        return FileResult(rel, "failed", [Problem(f"The database rejected this change: {exc}.", file=rel)])

    merged_plain = merged_model.model_dump(mode="json")
    ordered: tuple[str, ...] = ()
    if kind == "profile":
        # The operation may have reordered roles newest first: show what was stored. Dict equality
        # ignores order, so compare the order too.
        merged_plain, ordered = _export(space, kind, key), PROFILE_ORDERED
        changed = json.dumps(merged_plain) != json.dumps(file_plain)
    else:
        changed = merged_plain != file_plain
    if changed:
        space.write_back_yaml(rel, merged_plain, force=True,
                              file_plain=tidy_application(merged_plain) if kind == "application" else None, ordered=ordered)
    else:
        space.repo.put_sync_file(rel, file_hash(path), merged_plain, "applied", [p.to_dict() for p in problems], version)
    return FileResult(rel, "applied", problems)


def _event_history(theirs: dict, base: dict | None, ours: dict | None, rel: str, key: str):
    """Events are append-only (R6.4). Returns problems and the event-free views for merging."""
    problems: list[Problem] = []
    if theirs.get("job_id") != key:
        problems.append(Problem(f"`job_id` must stay `{key}` (the folder name). Job ids never change.", file=rel, field="job_id"))
    base_events = (base or ours or {}).get("events", []) if (base or ours) else []
    db_events = (ours or {}).get("events", [])
    file_events = theirs.get("events", [])
    if file_events[: len(base_events)] != base_events:
        n = next((i for i, (a, b) in enumerate(zip(file_events, base_events)) if a != b), min(len(file_events), len(base_events)))
        problems.append(Problem(
            f"Event {n + 1} was changed or removed, but recorded events can't change. Put it back as it was and add a `correction` event instead.",
            file=rel, field=f"events.{n}"))
        merged_events = file_events
    else:
        merged_events = db_events + file_events[len(base_events):]
    theirs = {**theirs, "events": merged_events}
    strip = lambda d: None if d is None else {k: v for k, v in d.items() if k != "events"}
    return problems, theirs, strip(base), strip(ours)


def _conflict_problem(rel: str, path: tuple, theirs: Any, ours: Any) -> Problem:
    def get(d, p):
        for k in p:
            if isinstance(d, dict):
                d = d.get(k)
            else:
                return None
        return d
    f = ".".join(str(x) for x in path) or "(whole file)"
    return Problem(
        f"Changed both in this file ({json.dumps(get(theirs, path))}) and by the app ({json.dumps(get(ours, path))}) since the last sync. "
        f"Run `rb resolve {rel} --keep file` or `--keep app`.",
        file=rel, field=f)


def _rules(space: UserSpace, kind: str, key: str | None, model: BaseModel, rel: str) -> list[Problem]:
    if kind == "library":
        return validate_library(model, rel)
    if kind == "registry":
        return validate_registry(model, rel)
    if kind == "profile":
        problems = []
        if key != "public" and not space.repo.application_row(key):
            problems.append(Problem(f"There's no application for `{key}` yet; its application.yaml must sync first.", file=rel))
        lib = space.matching_library()
        return problems + validate_profile(model, lib, rel, space.repo.archived_ids())
    if kind == "application":
        return validate_application(model, space.repo.load_registry(), rel)
    return []


def _export(space: UserSpace, kind: str, key: str | None):
    return {
        "settings": lambda: export_settings(space),
        "registry": lambda: export_registry(space),
        "library": lambda: export_library(space),
        "profile": lambda: export_profile(space, key),
        "application": lambda: export_application(space, key),
    }[kind]()


# The operation that applies each kind of YAML file, and its input.
_OPERATIONS: dict[str, Callable[[str | None, BaseModel], tuple[str, dict]]] = {
    "settings": lambda key, m: ("settings_replace", {"settings": m}),
    "registry": lambda key, m: ("registry_replace", {"registry": m}),
    "library": lambda key, m: ("library_replace", {"library": m}),
    "profile": lambda key, m: ("profile_write", {"key": key, "profile": m}),
    "application": lambda key, m: ("application_replace", {"application": m}),
}

_DRY_RUN = object()


def _run(space: UserSpace, ctx: RequestContext, op: str, payload: dict, own: Change, check_only: bool) -> Any:
    """Apply one file: `invoke()` the operation inside a transaction, all or nothing (R7.7).

    Change notifications are held until the transaction commits, then sent, except `own`: the
    change to the file being synced, which this adapter records itself. A dry run rolls everything
    back, audit entry included, and sends nothing.
    """
    from .operations import invoke

    with hold_changes() as held:
        try:
            with space.repo.transaction():
                result = invoke(space.app, ctx, op, payload)
                if check_only:
                    raise _DryRun()
        except _DryRun:
            return _DRY_RUN
    for sp, change in dict.fromkeys(held):
        if change != own:
            sp.notify(change)
    return result


def _problems(dicts: list[dict]) -> list[Problem]:
    fields = Problem.__dataclass_fields__
    return [Problem(**{k: v for k, v in d.items() if k in fields}) for d in dicts]


def _sync_markdown(space: UserSpace, ctx, rel, path: Path, row, check_only, ensure_backup, force_file) -> FileResult:
    text, problems = yamlio.read_markdown(path, rel)
    if text is None:
        return FileResult(rel, "failed", problems)
    text = text.strip("\n") + "\n" if text.strip() else ""
    parts = rel.split("/")
    job_id = parts[1]
    if not space.repo.application_row(job_id):
        return FileResult(rel, "waiting", [Problem(f"Waiting for jobs/{job_id}/application.yaml to sync.", file=rel)])
    base = json.loads(row["content"])["text"] if row and row["content"] else None

    if parts[-1] == "posting.md":
        ours = space.repo.application_row(job_id)["posting_text"]
        merged, conflicts = (text, []) if force_file else three_way(base, text, ours)
        if conflicts:
            return FileResult(rel, "conflict", [Problem("The posting was changed both here and by the app. Run `rb resolve` to pick one.", file=rel)])
        if not check_only:
            ensure_backup()
        if _run(space, ctx, "posting_replace", {"job_id": job_id, "text": merged}, Change("posting", job_id), check_only) is not _DRY_RUN:
            space.repo.put_sync_file(rel, file_hash(path), {"text": merged}, "applied", [])
        return FileResult(rel, "applied")

    if parts[-2] == "answers":
        kind, slug = "answer", parts[-1][:-3]
    else:
        kind, slug = DOC_FILES[parts[-1]]
    existing = space.repo.get_document(job_id, kind, slug)
    ours = existing["text"] if existing else None
    merged, conflicts = (text, []) if force_file else three_way(base, text, ours)
    if conflicts:
        return FileResult(rel, "conflict", [Problem("This document was changed both here and by the app. Run `rb resolve` to pick one.", file=rel)])
    if existing and merged == existing["text"]:
        if not check_only:
            space.repo.put_sync_file(rel, file_hash(path), {"text": merged}, "applied", [])
        return FileResult(rel, "applied")
    if not check_only:
        ensure_backup()
    # The owner's own edit: honesty findings are recorded as warnings, and the document goes back
    # to draft, so it needs approving again (D10, R9.12).
    question = existing["question"] if existing else DEFAULT_QUESTIONS.get(slug, slug.replace("-", " ").capitalize() + "?")
    payload = {"job_id": job_id, "kind": kind, "text": merged,
               **({"slug": slug, "question": question} if kind == "answer" else {})}
    out = _run(space, ctx, "document_propose", payload, Change("document", job_id, kind, slug), check_only)
    if out is _DRY_RUN:
        return FileResult(rel, "applied")
    stored = space.repo.get_document(job_id, kind, slug)["text"]
    # The file had unsynced edits until now, so the usual write-back would refuse: force it.
    space.write_back_text(rel, doc_header(job_id, kind, slug, "draft") + stored, stored, force=True)
    return FileResult(rel, "applied", _problems(out.get("warnings", [])))


def _resolve_keep_app(space: UserSpace, rel: str) -> None:
    kind, key = _kind(rel)
    if kind in ("settings", "registry", "library"):
        data = _export(space, kind, key)
        if data:
            space.write_back_yaml(rel, data, force=True)
    elif kind in ("profile", "application"):
        data = _export(space, kind, key)
        if data:
            space.write_back_yaml(rel, data, force=True)
    elif rel.endswith("posting.md"):
        job = rel.split("/")[1]
        text = space.repo.application_row(job)["posting_text"] or ""
        space.write_back_text(rel, text, text, force=True)
    else:
        parts = rel.split("/")
        k, slug = ("answer", parts[-1][:-3]) if parts[-2] == "answers" else DOC_FILES[parts[-1]]
        row = space.repo.get_document(parts[1], k, slug)
        if row:
            space.write_back_text(rel, doc_header(parts[1], k, slug, row["status"]) + row["text"], row["text"], force=True)


# ---------------------------------------------------------------- status file (R7.9)
def used_intake_files(space: UserSpace) -> list[tuple[str, str]]:
    """(file, job id) for intake files that started a job and still hold that same text: safe to delete."""
    from .models import content_hash

    out = []
    for row in space.repo.file_posting_captures():
        path = space.data_dir / row["source_path"]
        if not path.is_file():
            continue
        text, _ = yamlio.read_text_safely(path, row["source_path"])
        if text is not None and content_hash(text) == row["content_hash"]:
            out.append((row["source_path"], row["job_id"]))
    return sorted(set(out))



def write_status_file(space: UserSpace, report: SyncReport) -> None:
    from .db import now_iso
    lines = [
        "# Sync status",
        "",
        f"Last sync: {now_iso()} (written by resume-builder; don't edit; it is rewritten on every sync)",
        "",
    ]
    problems = [r for r in space.repo.sync_files() if r["result"] in ("failed", "conflict", "waiting")]
    if not problems and not report.by("missing"):
        lines.append("Everything is in sync. ✓")
    else:
        lines.append("These files need attention. The app keeps using their last good version until they're fixed.")
        lines.append("")
        for row in problems:
            lines.append(f"## {row['path']} ({row['result']})")
            lines.append("")
            for prob in json.loads(row["problems"]):
                lines.append("- " + Problem(**prob).render())
            lines.append("")
        for r in report.by("missing"):
            lines.append(f"## {r.rel} (missing)")
            lines += ["- " + p.render() for p in r.problems] + [""]
    proposals = space.repo.list_intake_proposals("pending")
    if proposals:
        lines += ["", "## Jobs proposed by your AI agent", "",
                  "Nothing is created until you confirm. Review each with `rb confirm-job <n>`, or `rb discard-job <n>`.", ""]
        for row in proposals:
            title = next((f["value"] for f in json.loads(row["data"])["fields"] if f["name"] == "title"), "(no title)")
            lines.append(f"- #{row['id']}: {title} (proposed {row['created_at'][:10]})")
    used = used_intake_files(space)
    if used:
        lines += ["", "## Intake files already used", "",
                  "These postings are saved with their jobs. You can delete the files (the app never does).", ""]
        lines += [f"- `{rel}`: started `{job}`" for rel, job in used]
    warnings = [(r.rel, p) for r in report.results for p in r.problems if p.severity == "warning" and r.result == "applied"]
    if warnings:
        lines += ["", "## Warnings (not blocking)", ""]
        lines += [f"- {p.render()}" for _, p in warnings]
    yamlio.write_text(space.data_dir / "SYNC-STATUS.md", "\n".join(lines).rstrip() + "\n")
