"""Operations of the YAML/Markdown input adapter (PRD 8.1, 8.7, 8.10).

The user's folder sits on top of the service API: this module creates and repairs it, writes the
JSON Schemas for editors, runs the sync, and writes changed data back to the working copies when a
service reports a change (`write_back`, the App's default change listener). Services never touch
these files themselves.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import sync as sync_mod
from . import yamlio
from .changes import Change
from .errors import NotFound, ValidationFailed
from .models import Application, ContentLibrary, OrganizationRegistry, Profile, UserSettings
from .operations import operation
from .security import RequestContext
from .services.profiles import ProfileWriteIn, profile_write
from .services.space_ops import status
from .slug import is_slug
from .space import App, UserSpace, file_hash, render_seed


class Empty(BaseModel):
    pass


SEED_FILES = {
    "user.yaml": "user.yaml",
    "base.yaml": "base.yaml",
    "organizations.yaml": "organizations.yaml",
    "profiles/public.yaml": "public.yaml",
    "START-HERE.md": "START-HERE.md",
    "intake/README.md": "intake-README.md",
}


class InitUserIn(BaseModel):
    display_name: str | None = None
    timezone: str = "UTC"


def seed_values(space: UserSpace, rel: str, extra: dict | None = None) -> dict:
    return {
        "schema_prefix": "../" * rel.count("/"),
        "display_name": space.user.replace("-", " ").title(),
        "timezone": "UTC",
        "today": date.today().isoformat(),
        "user": space.user,
        **(extra or {}),
    }


@operation("space_init", "space:init", InitUserIn, "Create a user space from the seed (never overwrites).", writes=True)
def space_init(space: UserSpace, ctx: RequestContext, data: InitUserIn) -> dict:
    created = repair(space, ctx, {"display_name": data.display_name or space.user.replace("-", " ").title(), "timezone": data.timezone})
    write_schemas(space)
    report = sync_mod.run_sync(space, ctx)
    return {"user": space.user, "created": created, "sync": report.to_dict()}


class DeleteUserIn(BaseModel):
    confirm: str = Field(description="The user's name again, exactly: nothing is deleted without it")
    delete_backups: bool = Field(False, description="Also delete the user's backups. Default: keep them, plus a final one")


@operation("space_delete", "space:delete", DeleteUserIn,
           "Delete a user: their data folder, database and exports, and optionally their backups. Can't be undone.", writes=True)
def space_delete(space: UserSpace, ctx: RequestContext, data: DeleteUserIn) -> dict:
    """The one way to delete a user (`rb delete-user`). Backs up first unless the backups go too.

    Refuses the default user (bootstrap would recreate them empty at the next start) and any name
    that isn't typed back exactly. The user's files go through this adapter, exports and backups
    through their FileStores, the database through Storage.
    """
    user = space.user
    if data.confirm != user:
        raise ValidationFailed(f"Type the user's name, `{user}`, to confirm. Nothing was deleted.")
    if user == space.app.settings.default_user:
        raise ValidationFailed(
            f"`{user}` is your default user (RB_DEFAULT_USER), so the app would create it again, empty, at the next start. "
            "Set RB_DEFAULT_USER in .env to another user and restart the app first. Nothing was deleted.")
    import shutil

    from .filestore import delete_all

    if not hasattr(space.repo, "destroy"):
        raise ValidationFailed("This storage backend can't delete a user. Nothing was deleted.")
    space.audit(ctx, "space:delete", target=user, result="ok")   # kept in the final backup
    final_backup = None if data.delete_backups else space.backup("before-delete")
    exports = delete_all(space.output)
    backups = delete_all(space.backups) if data.delete_backups else 0
    if space.data_dir.exists():
        shutil.rmtree(space.data_dir)
    space.lock_path.unlink(missing_ok=True)
    with space.app._lock:
        space.app._spaces.pop(user, None)
    space.repo.destroy()
    space.destroyed = True
    return {"deleted": user, "exports_deleted": exports, "backups_deleted": backups,
            "backups_kept": None if data.delete_backups else space.backups.location(),
            "final_backup": final_backup}


def repair(space: UserSpace, ctx: RequestContext, values: dict | None = None) -> list[str]:
    """Create missing folders and files. Never overwrite (R10.5, R10.6).

    A missing file whose data is already in the database is rewritten from the database, not
    from the seed, so deleting base.yaml by accident can't wipe the content library.
    """
    created = []
    for d in (space.data_dir, space.data_dir / "profiles", space.data_dir / "jobs", space.data_dir / "intake"):
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
    known = {
        "user.yaml": lambda: sync_mod.write_back_all(space, "settings"),
        "base.yaml": lambda: sync_mod.write_back_all(space, "library"),
        "organizations.yaml": lambda: sync_mod.export_registry(space) and sync_mod.write_back_all(space, "registry"),
        "profiles/public.yaml": lambda: sync_mod.export_profile(space, "public") and sync_mod.write_back_all(space, "profile", "public"),
    }
    for rel, seed in SEED_FILES.items():
        p = space.data_dir / rel
        if p.exists():
            continue
        if rel in known and known[rel]():
            created.append(rel + " (from database)")
            continue
        text = (space.app.settings.seed_dir / seed).read_text(encoding="utf-8")
        yamlio.write_text(p, render_seed(text, seed_values(space, rel, values)))
        created.append(rel)
    if created:
        space.audit(ctx, "space:repair", target=",".join(created))
    return created


# ---------------------------------------------------------------- JSON Schemas (R10.14, R11.3)

SCHEMA_MODELS = {
    "user": UserSettings, "base": ContentLibrary, "organizations": OrganizationRegistry,
    "profile": Profile, "application": Application,
}


def write_schemas(space: UserSpace) -> None:
    out = space.data_dir / ".schemas"
    reg = space.repo.load_registry()
    companies = [c.slug for c in reg.companies]
    departments = sorted({d.slug for c in reg.companies for d in c.departments})
    for name, model in SCHEMA_MODELS.items():
        schema = model.model_json_schema()
        if name == "application":
            props = schema.get("properties", {})
            if companies:
                props["company"] = {**props.get("company", {}), "enum": companies}
            if departments:
                props["department"] = {"anyOf": [{"type": "string", "enum": departments}, {"type": "null"}], "default": None}
        yamlio.write_text(out / f"{name}.schema.json", json.dumps(schema, indent=2) + "\n")


# ---------------------------------------------------------------- bootstrap / doctor

def check_mounts(app: App) -> list[str]:
    problems = []
    for label, root in (("data", app.settings.data_root), ("db", app.settings.db_root),
                        ("output", app.settings.output_root), ("backups", app.settings.backup_root)):
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".rb-write-test"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            problems.append(
                f"The {label} folder ({root}) is not writable: {exc.strerror}. Check the `{label}` mount in compose.yaml "
                f"and that PUID/PGID in .env match your user (`id -u` / `id -g`).")
    return problems


def bootstrap(app: App, check_only: bool = False) -> dict:
    """Run on every server start (R10.1). Returns a report; raises SystemExit on broken mounts."""
    mount_problems = check_mounts(app)
    if mount_problems:
        raise SystemExit("resume-builder can't start:\n- " + "\n- ".join(mount_problems))
    users, folder_problems = app.discover_users()
    report: dict = {"users": {}, "folders": folder_problems, "created_user": None, "hint": None}
    if not users:
        default = app.settings.default_user
        if default and not check_only:
            if not is_slug(default):
                raise SystemExit(f"RB_DEFAULT_USER `{default}` is not a valid name. Use lowercase letters, digits and hyphens.")
            from .operations import invoke
            invoke(app, RequestContext.system(default), "space_init", {})
            report["created_user"] = default
            users = [default]
        else:
            report["hint"] = ("No user folder found. Set RB_DEFAULT_USER in .env and restart, or run: "
                              "docker compose run --rm app rb init-user <your-name>")
    for user in users:
        space = app.space(user)
        ctx = RequestContext.system(user)
        entry: dict = {}
        if not check_only:
            entry["created"] = repair(space, ctx)
            entry["daily_backup"] = str(space.daily_backup_if_due() or "")
            write_schemas(space)
            entry["sync"] = sync_mod.run_sync(space, ctx).to_dict()
        else:
            entry["sync"] = sync_mod.run_sync(space, ctx, check_only=True).to_dict()
        entry["status"] = status(space)
        report["users"][user] = entry
    return report


class SyncIn(BaseModel):
    check_only: bool = False


@operation("sync", "space:sync", SyncIn, "Validate changed input files and apply them to the database.", writes=True)
def sync_op(space: UserSpace, ctx: RequestContext, data: SyncIn) -> dict:
    return sync_mod.run_sync(space, ctx, check_only=data.check_only).to_dict()


class ResolveIn(BaseModel):
    file: str = Field(description="Path relative to the user folder, e.g. jobs/<job-id>/application.yaml")
    keep: str = Field(pattern="^(file|app)$")


@operation("resolve", "space:sync", ResolveIn, "Resolve a sync conflict by keeping the file's or the app's version.", writes=True)
def resolve_op(space: UserSpace, ctx: RequestContext, data: ResolveIn) -> dict:
    rel = user_rel(data.file, space.user, f"`{data.file}` isn't a file in your folder. "
                                          "Example: jobs/<job-id>/application.yaml (as `rb status` lists it)")
    if not (space.data_dir / rel).exists():
        raise NotFound(f"`{rel}` doesn't exist in your folder. `rb status` lists the files with conflicts.")
    return sync_mod.run_sync(space, ctx, prefer={rel: data.keep}).to_dict()


@operation("export_files", "space:sync", Empty, "Rewrite missing working-copy files from the database.", writes=True)
def export_files(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    written = repair(space, ctx)
    for job_id in space.repo.job_ids(include_archived=False):
        for what, rel in (("application", f"jobs/{job_id}/application.yaml"), ("posting", f"jobs/{job_id}/posting.md"),
                          ("profile", f"jobs/{job_id}/profile.yaml")):
            if not (space.data_dir / rel).exists() and sync_mod.write_back_all(space, what, job_id):
                written.append(rel)
        for row in space.repo.list_documents(job_id):
            rel = sync_mod.doc_rel(job_id, row["kind"], row["slug"])
            if not (space.data_dir / rel).exists() and sync_mod.write_back_document(space, job_id, row["kind"], row["slug"]):
                written.append(rel)
    return {"written": written}




# ---------------------------------------------------------------- write-back on service changes (R7.12)

def write_back(space: UserSpace, change: Change) -> bool:
    """The default change listener: update the working copy that shows the changed data."""
    kind, key = change.kind, change.key
    if kind == "document":
        return sync_mod.write_back_document(space, key, change.doc_kind, change.slug)
    if kind == "job_created":
        written = sync_mod.write_back_all(space, "application", key)
        seed_posting(space, key)
        return written
    written = sync_mod.write_back_all(space, kind, key)
    if kind == "registry":
        write_schemas(space)  # the company and department pick lists in editors
    return written


def seed_posting(space: UserSpace, job_id: str) -> None:
    rel = f"jobs/{job_id}/posting.md"
    if (space.data_dir / rel).exists():
        return
    text = (space.app.settings.seed_dir / "posting.md").read_text(encoding="utf-8").replace("{{job_id}}", job_id)
    yamlio.write_text(space.data_dir / rel, text)
    # The seeded posting is only hints, not a posting: record it as already synced and empty,
    # so the posting text is set the first time the user edits the file.
    space.repo.put_sync_file(rel, file_hash(space.data_dir / rel), {"text": ""}, "applied", [])


# ---------------------------------------------------------------- profiles pasted as YAML (copy-and-paste channel)

class ProfileTextIn(BaseModel):
    key: str = Field(description="`public` or a job id")
    text: str = Field(description="The profile as YAML, e.g. pasted from a chat assistant (a ```yaml fence is fine)")


@operation("profile_propose_text", "profile:write", ProfileTextIn, "Save a profile given as YAML text.", writes=True)
def profile_propose_text(space: UserSpace, ctx: RequestContext, data: ProfileTextIn) -> dict:
    from pydantic import ValidationError

    from .validation import pydantic_problems

    rel = "pasted profile.yaml"
    doc, problems = yamlio.parse_yaml_text(data.text, rel)
    if doc is None:
        raise ValidationFailed("The profile isn't valid YAML.", problems)
    plain = yamlio.to_plain(doc)
    plain.setdefault("schema_version", 1)
    try:
        profile = Profile.model_validate(plain)
    except ValidationError as exc:
        raise ValidationFailed("The profile doesn't match the expected format.", pydantic_problems(exc, rel, doc, yamlio.line_of))
    return profile_write(space, ctx, ProfileWriteIn(key=data.key, profile=profile))


# ---------------------------------------------------------------- job intake from the user's intake folder (R13.2, R13.4, D25)

INTAKE_DIR = "intake"


class IntakeFileIn(BaseModel):
    file: str = Field(description="A text file in your intake folder, e.g. intake/acme-sre.txt (or just acme-sre.txt)")
    url: str | None = Field(None, description="The posting's link, recorded as its link (not fetched)")


def user_rel(name: str, user: str | None, problem: str) -> str:
    """A file named by the user, as a POSIX path relative to their folder.

    `rb` runs in the container, but people type paths as their computer sees them: with backslashes,
    from the repo folder (`.\\data\\<user>\\jobs\\...`) or in full (`C:\\...\\data\\<user>\\...`). Such a
    path is accepted when it goes through this user's folder: the part after the last `<user>/` is used.
    Anything else absolute, or with `..`, is refused with `problem`."""
    rel = name.strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    parts = rel.split("/")
    if user:
        for i in range(len(parts) - 2, -1, -1):
            if parts[i] == user:          # inside this user's folder: keep what follows it
                parts = parts[i + 1:]
                break
    if not parts or parts[0] == "" or ":" in parts[0] or ".." in parts:   # absolute, a drive, or climbing out
        raise ValidationFailed(problem)
    rel = "/".join(p for p in parts if p not in ("", "."))
    if not rel:
        raise ValidationFailed(problem)
    return rel


def intake_rel(name: str, user: str | None = None) -> str:
    """`intake/<name>` for a file named relative to the user's folder or to the intake folder
    (or by a host path into this user's intake folder, see `user_rel`)."""
    rel = user_rel(name, user, f"`{name}` isn't a file in your intake folder. Example: intake/acme-sre.txt")
    return rel if rel.startswith(INTAKE_DIR + "/") else f"{INTAKE_DIR}/{rel}"


@operation("job_intake_preview_file", "application:write", IntakeFileIn,
           "Like job_intake_preview, from a posting saved as a text file in the user's intake folder.", writes=True)
def job_intake_preview_file(space: UserSpace, ctx: RequestContext, data: IntakeFileIn) -> dict:
    """The file adapter reads the file (safe-reading limits, R7.7); the service does the rest.
    Only files inside the intake folder are read, and only when asked; nothing there is changed."""
    from .services.intake import preview_text

    rel, text = read_intake_file(space, data.file, "the posting text", "acme-sre.txt")
    return preview_text(space, ctx, text, source_kind="file", source_path=rel, url=data.url)


def intake_path(space: UserSpace, name: str, what: str, example: str) -> tuple[str, Path]:
    """(rel, resolved path) of a file in the user's intake folder. Only files inside the intake folder
    are read, and only when asked; nothing there is changed."""
    rel = intake_rel(name, space.user)
    folder = (space.data_dir / INTAKE_DIR).resolve()
    try:
        real = (space.data_dir / rel).resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise NotFound(f"There's no `{rel}` in your folder. Save {what} there first (see {INTAKE_DIR}/README.md).") from None
    if not real.is_relative_to(folder) or not real.is_file():
        raise ValidationFailed(f"`{rel}` isn't a file in your intake folder.")
    if real.name.lower() == "readme.md" and real.parent == folder:
        raise ValidationFailed(f"{INTAKE_DIR}/README.md explains the folder. Save {what} in a file of its own, "
                               f"for example {INTAKE_DIR}/{example}.")
    return rel, real


def read_intake_file(space: UserSpace, name: str, what: str, example: str) -> tuple[str, str]:
    """(rel, text) of a text file in the user's intake folder, read with the safe-reading limits (R7.7)."""
    rel, real = intake_path(space, name, what, example)
    text, problems = yamlio.read_text_safely(real, rel)
    if text is None:
        raise ValidationFailed(f"Can't read `{rel}`.", problems)
    return rel, text


class ImportFileIn(BaseModel):
    file: str = Field(description="Your resume saved in your intake folder, e.g. intake/resume.docx (or .pdf, .html, .md, .txt)")
    format: Literal["auto", "html", "markdown", "text", "docx", "pdf"] = Field(
        "auto", description="auto detects it from the file's first bytes and its name")
    dry_run: bool = Field(True, description="Report what would be imported without changing anything. Set false to add the drafts.")
    expect_hash: str | None = Field(None, description="The content_hash a dry run returned: refuse if the resume "
                                                      "changed since (the CLI's preview, then import)")


SAVE_AS_TEXT = "Save or export it as .docx, HTML or plain text and import that file instead."


@operation("library_import_file", "content:propose", ImportFileIn,
           "Like library_import, from a resume saved in the user's intake folder: Word (.docx), PDF, HTML, Markdown "
           "or text. Dry run by default.", writes=True)
def library_import_file(space: UserSpace, ctx: RequestContext, data: ImportFileIn) -> dict:
    """The file adapter reads the file; the service does the rest (issues #1, #39). The file is never
    changed. Word and PDF files are converted to text first (resume_readers), within size limits."""
    from . import resume_readers as readers
    from .services.library_import import import_resume

    rel, real = intake_path(space, data.file, "your resume", "resume.docx")
    with real.open("rb") as f:
        head = f.read(8)
    kind = data.format if data.format in (readers.DOCX, readers.PDF) else (
        readers.sniff(head, rel) if data.format == "auto" else None)
    if kind == readers.DOC:
        raise ValidationFailed(f"`{rel}` is in the old Word format (.doc), which can't be read. "
                               "Open it in Word and save it as .docx (File > Save As), then import the .docx file.")
    if kind in (readers.DOCX, readers.PDF):
        if real.stat().st_size > readers.MAX_DOCUMENT_BYTES:
            raise ValidationFailed(f"Can't read `{rel}`: it's larger than {readers.MAX_DOCUMENT_BYTES // 1_000_000} MB. {SAVE_AS_TEXT}")
        raw = real.read_bytes()
        try:
            text = readers.docx_to_html(raw) if kind == readers.DOCX else readers.pdf_to_text(raw)
        except readers.ReadFailed as exc:
            raise ValidationFailed(f"Can't read `{rel}`: {exc.message}. {SAVE_AS_TEXT}") from None
        out = import_resume(space, ctx, text, "html" if kind == readers.DOCX else "text", rel, data.dry_run,
                            expect_hash=data.expect_hash, source_format=kind)
        return {"file": rel, **out}
    rel, text = read_intake_file(space, data.file, "your resume", "resume.docx")
    out = import_resume(space, ctx, text, data.format, rel, data.dry_run, expect_hash=data.expect_hash)
    return {"file": rel, **out}

