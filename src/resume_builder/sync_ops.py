"""Operations of the YAML/Markdown input adapter (PRD 8.1, 8.7, 8.10).

The user's folder sits on top of the service API: this module creates and repairs it, writes the
JSON Schemas for editors, runs the sync, and writes changed data back to the working copies when a
service reports a change (`write_back`, the App's default change listener). Services never touch
these files themselves.
"""

from __future__ import annotations

import json
from datetime import date

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


def repair(space: UserSpace, ctx: RequestContext, values: dict | None = None) -> list[str]:
    """Create missing folders and files. Never overwrite (R10.5, R10.6).

    A missing file whose data is already in the database is rewritten from the database, not
    from the seed, so deleting base.yaml by accident can't wipe the content library.
    """
    created = []
    for d in (space.data_dir, space.data_dir / "profiles", space.data_dir / "jobs"):
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
    if not (space.data_dir / data.file).exists():
        raise NotFound(f"`{data.file}` doesn't exist in your folder.")
    return sync_mod.run_sync(space, ctx, prefer={data.file: data.keep}).to_dict()


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
