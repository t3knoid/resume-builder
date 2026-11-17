"""Whole-item replace operations (issue #5).

Each one saves a complete settings file, organization registry, content library, application or
posting in one call, with the same rules as the smaller operations: removing something archives it
(never deletes it, R7.10), and recorded events can't change (append-only, R6.4).

The YAML/Markdown sync adapter applies every changed input file through these (and through
`profile_write` and `document_propose`), as the owner on the `yaml-sync` channel, so file edits get
the same permission check, validation and audit as the CLI and API. A host without user files can
use them to import or replace a user's data in bulk.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from ..errors import NotFound, Problem, ValidationFailed
from ..models import Application, ContentLibrary, OrganizationRegistry, UserSettings
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace
from ..validation import stage_after, validate_application, validate_library, validate_registry


def _errors_or_warnings(problems, message: str) -> list[dict]:
    if any(p.severity == "error" for p in problems):
        raise ValidationFailed(message, problems)
    return [p.to_dict() for p in problems]


class SettingsReplaceIn(BaseModel):
    settings: UserSettings


@operation("settings_replace", "settings:write", SettingsReplaceIn,
           "Replace the user's settings (user.yaml) as a whole.", writes=True)
def settings_replace(space: UserSpace, ctx: RequestContext, data: SettingsReplaceIn) -> dict:
    from .render import template_problems
    s = data.settings
    _errors_or_warnings(template_problems(space, s.template, s.template_version), "user.yaml has problems.")
    with space.repo.transaction():
        space.repo.put_settings(data.settings, ctx.actor)
    return {"saved": "settings", "file_updated": space.changed("settings")}


class RegistryReplaceIn(BaseModel):
    registry: OrganizationRegistry


@operation("registry_replace", "organization:write", RegistryReplaceIn,
           "Replace the organization registry as a whole. Companies and departments left out are archived, never deleted.",
           writes=True)
def registry_replace(space: UserSpace, ctx: RequestContext, data: RegistryReplaceIn) -> dict:
    warnings = _errors_or_warnings(validate_registry(data.registry), "The organization registry has problems.")
    with space.repo.transaction():
        space.repo.replace_registry(data.registry, ctx.actor)
    return {"saved": "registry", "warnings": warnings, "file_updated": space.changed("registry")}


class LibraryReplaceIn(BaseModel):
    library: ContentLibrary


@operation("library_replace", "content:confirm", LibraryReplaceIn,
           "Replace the content library as a whole, statuses included (so only the owner may). "
           "Items left out are archived, never deleted.", writes=True)
def library_replace(space: UserSpace, ctx: RequestContext, data: LibraryReplaceIn) -> dict:
    warnings = _errors_or_warnings(validate_library(data.library), "The content library has problems.")
    with space.repo.transaction():
        space.repo.replace_library(data.library, ctx.actor)
    return {"saved": "library", "warnings": warnings, "file_updated": space.changed("library")}


class ApplicationReplaceIn(BaseModel):
    application: Application


@operation("application_replace", "application:write", ApplicationReplaceIn,
           "Replace an application as a whole, creating it if needed. Recorded events must stay as they are; "
           "events after them are appended.", writes=True)
def application_replace(space: UserSpace, ctx: RequestContext, data: ApplicationReplaceIn) -> dict:
    app = data.application
    job_id = app.job_id
    rel = f"jobs/{job_id}/application.yaml"
    problems = validate_application(app, space.repo.load_registry(), rel)
    if space.repo.application_row(job_id):
        recorded = [e.model_dump(mode="json") for e in space.repo.get_application(job_id).events]
        given = [e.model_dump(mode="json") for e in app.events]
        if given[: len(recorded)] != recorded:
            n = next((i for i, (a, b) in enumerate(zip(given, recorded)) if a != b), min(len(given), len(recorded)))
            raise ValidationFailed("Recorded events can't change.", [Problem(
                f"Event {n + 1} was changed or removed, but recorded events can't change. Put it back as it was "
                "and add a `correction` event instead.", file=rel, field=f"events.{n}")])
    warnings = _errors_or_warnings(problems, "The application has problems.")
    stage, _ = stage_after(app.events)
    with space.repo.transaction():
        space.repo.save_application(app, stage, ctx.actor)
    # Snapshots the application lists as sent are protected from automatic deletion (R12.10).
    from .snapshots import mark_sent_from_application
    mark_sent_from_application(space, job_id)
    return {"job_id": job_id, "stage": stage, "warnings": warnings, "file_updated": space.changed("application", job_id)}


class PostingReplaceIn(BaseModel):
    job_id: str
    text: Annotated[str, StringConstraints(max_length=1_000_000)] = Field(description="The posting text (Markdown); empty clears it")


@operation("posting_replace", "application:write", PostingReplaceIn, "Replace a job's posting text.", writes=True)
def posting_replace(space: UserSpace, ctx: RequestContext, data: PostingReplaceIn) -> dict:
    if not space.repo.application_row(data.job_id):
        raise NotFound(f"No job `{data.job_id}`.")
    with space.repo.transaction():
        space.repo.set_posting(data.job_id, data.text)
    return {"job_id": data.job_id, "file_updated": space.changed("posting", data.job_id)}
