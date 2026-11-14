"""Applications and their append-only event log (PRD 8.6, R1.3, R10.9)."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from ..errors import NotFound, ValidationFailed
from ..models import Application, Event
from ..operations import operation
from ..security import RequestContext
from ..slug import build_job_id
from ..space import UserSpace
from ..validation import stage_after, validate_application
from .orgs import AddCompanyIn, AddDeptIn, company_add, department_add, find_matches


class Empty(BaseModel):
    pass


def today_for(space: UserSpace) -> date:
    try:
        return datetime.now(ZoneInfo(space.user_settings.timezone)).date()
    except Exception:
        return date.today()


class NewJobIn(BaseModel):
    company: str = Field(description="Company slug or name. Existing registry entries are used first.")
    department: str | None = Field(None, description="Department slug or name")
    title: str | None = None
    posting_url: str | None = None
    source: str | None = None
    create_missing: bool = Field(False, description="Add the company/department to the registry if no match exists")


@operation("job_new", "application:write", NewJobIn, "Start tracking a job: builds the job id and seeds its folder.", writes=True)
def job_new(space: UserSpace, ctx: RequestContext, data: NewJobIn) -> dict:
    company_slug, dept_slug = resolve_org(space, ctx, data.company, data.department, data.create_missing)
    entered = today_for(space)
    job_id = build_job_id(entered, company_slug, dept_slug, set(space.repo.job_ids()))
    app = Application(
        job_id=job_id, company=company_slug, department=dept_slug, title=data.title,
        posting_url=data.posting_url, source=data.source if data.source in (None, "linkedin", "referral", "company_site", "recruiter", "job_board", "other") else "other",
        events=[Event(date=entered, type="stage_change", stage="interested", notes="Started tracking")],
    )
    problems = validate_application(app, space.repo.load_registry(), f"jobs/{job_id}/application.yaml")
    if any(p.severity == "error" for p in problems):
        raise ValidationFailed("Can't create the job.", problems)
    with space.repo.transaction():
        space.repo.save_application(app, "interested", ctx.actor)
    space.changed("job_created", job_id)
    return {"job_id": job_id, "folder": f"jobs/{job_id}/", "next": f"Paste the job description into jobs/{job_id}/posting.md"}


def resolve_org(space: UserSpace, ctx: RequestContext, company: str, department: str | None, create: bool) -> tuple[str, str | None]:
    reg = space.repo.load_registry()
    comp = next((c for c in reg.companies if c.slug == company), None)
    if comp is None:
        matches = find_matches(reg.companies, company)
        if matches:
            comp = matches[0]
        elif create:
            slug = company_add(space, ctx, AddCompanyIn(name=company, force=True))["slug"]
            comp = next(c for c in space.repo.load_registry().companies if c.slug == slug)
        else:
            raise NotFound(f"No company matches `{company}`. Pick one from `rb orgs`, or add it (new-job asks, or pass --create).")
    if not department:
        return comp.slug, None
    dept = next((d for d in comp.departments if d.slug == department), None)
    if dept is None:
        matches = find_matches(comp.departments, department)
        if matches:
            dept = matches[0]
        elif create:
            return comp.slug, department_add(space, ctx, AddDeptIn(company=comp.slug, name=department, force=True))["slug"]
        else:
            raise NotFound(f"No department under `{comp.slug}` matches `{department}`.")
    return comp.slug, dept.slug


class JobIn(BaseModel):
    job_id: str


@operation("job_get", "application:read", JobIn, "One application with its events, contacts and posting.")
def job_get(space: UserSpace, ctx: RequestContext, data: JobIn) -> dict:
    app = space.repo.get_application(data.job_id)
    row = space.repo.application_row(data.job_id)
    out = app.model_dump(mode="json")
    out["stage"] = row["stage"]
    out["archived"] = bool(row["archived"])
    out["posting_text"] = row["posting_text"]
    out["event_log"] = [{**json.loads(e["data"]), "seq": e["seq"], "actor": e["actor"], "recorded_at": e["created_at"]} for e in space.repo.event_rows(data.job_id)]
    out["event_drafts"] = [{"draft_id": d["id"], **json.loads(d["data"]), "by": d["created_by"]} for d in space.repo.event_drafts(data.job_id)]
    return out


class ListIn(BaseModel):
    include_archived: bool = False
    stage: str | None = None
    company: str | None = None
    department: str | None = None


@operation("job_list", "application:read", ListIn, "Applications, newest first, with stage and stale flags.")
def job_list(space: UserSpace, ctx: RequestContext, data: ListIn) -> list[dict]:
    stale_days = space.user_settings.tracker.stale_days
    today = today_for(space)
    out = []
    for row in space.repo.list_application_rows(data.include_archived):
        if data.stage and row["stage"] != data.stage:
            continue
        if data.company and row["company_slug"] != data.company:
            continue
        if data.department and row["department_slug"] != data.department:
            continue
        d = json.loads(row["data"])
        events = space.repo.event_rows(row["job_id"])
        last = max((date.fromisoformat(e["date"]) for e in events), default=None)
        applied = next((e["date"] for e in events if e["stage"] == "applied"), None)
        out.append({
            "job_id": row["job_id"], "company": row["company_slug"], "department": row["department_slug"],
            "title": d.get("title"), "stage": row["stage"], "applied_on": applied,
            "last_event": last.isoformat() if last else None,
            "stale": bool(last and row["stage"] not in ("accepted", "declined", "rejected", "withdrawn", "ghosted")
                          and (today - last).days >= stale_days),
            "next_action": d.get("next_action"), "next_action_due": d.get("next_action_due"),
            "source": d.get("source"), "archived": bool(row["archived"]),
        })
    return out


class EventIn(BaseModel):
    job_id: str
    event: Event


@operation("event_append", "event:append", EventIn, "Append an event to an application (append-only).", writes=True)
def event_append(space: UserSpace, ctx: RequestContext, data: EventIn) -> dict:
    return _append(space, ctx, data.job_id, data.event)


def _append(space: UserSpace, ctx: RequestContext, job_id: str, event: Event) -> dict:
    app = space.repo.get_application(job_id)
    app.events.append(event)
    if event.type == "stage_change" and event.stage == "applied" or event.type == "document_sent":
        _fill_submission(space, app, event)
    problems = validate_application(app, space.repo.load_registry(), f"jobs/{job_id}/application.yaml")
    if any(p.severity == "error" for p in problems):
        raise ValidationFailed("That event can't be added.", problems)
    stage, _ = stage_after(app.events)
    with space.repo.transaction():
        space.repo.save_application(app, stage, ctx.actor)
    from .snapshots import mark_sent_from_application
    mark_sent_from_application(space, job_id)
    written = space.changed("application", job_id)
    return {"job_id": job_id, "stage": stage, "event_number": len(app.events),
            "file_updated": written, **({} if written else {"note": "application.yaml has unsynced edits; the next sync merges this event in."})}


def _fill_submission(space: UserSpace, app: Application, event: Event) -> None:
    """Record which snapshots were sent, by document type (R9.13)."""
    sub = app.submission
    for sid in event.documents:
        row = space.repo.get_snapshot(sid)
        if row is None:
            raise NotFound(f"No snapshot `{sid}`. List them with `rb snapshots {app.job_id}`.")
        if row["preview"]:
            raise ValidationFailed(f"`{sid}` is a preview and can't be sent. Render without --preview once its content is confirmed.")
        if row["doc_type"] == "resume":
            sub.resume_snapshot = sid
        elif row["doc_type"] == "cover_letter":
            sub.cover_letter_snapshot = sid
        elif row["doc_type"] == "answer" and sid not in sub.answer_snapshots:
            sub.answer_snapshots.append(sid)


@operation("event_propose", "event:propose", EventIn, "Propose an event (the agent drafts; the owner confirms).", writes=True)
def event_propose(space: UserSpace, ctx: RequestContext, data: EventIn) -> dict:
    space.repo.get_application(data.job_id)
    with space.repo.transaction():
        draft_id = space.repo.add_event_draft(data.job_id, data.event, ctx.actor)
    return {"draft_id": draft_id, "next": f"The owner confirms with `rb confirm-event {draft_id}` or discards with `rb discard-event {draft_id}`."}


class DraftIn(BaseModel):
    draft_id: int


@operation("event_confirm", "event:append", DraftIn, "Confirm a proposed event and append it.", writes=True)
def event_confirm(space: UserSpace, ctx: RequestContext, data: DraftIn) -> dict:
    row = next((d for d in space.repo.event_drafts() if d["id"] == data.draft_id), None)
    if row is None:
        raise NotFound(f"No event draft {data.draft_id}.")
    result = _append(space, ctx, row["job_id"], Event.model_validate_json(row["data"]))
    with space.repo.transaction():
        space.repo.delete_event_draft(data.draft_id)
    return result


@operation("event_discard", "event:append", DraftIn, "Discard a proposed event.", writes=True)
def event_discard(space: UserSpace, ctx: RequestContext, data: DraftIn) -> dict:
    with space.repo.transaction():
        space.repo.delete_event_draft(data.draft_id)
    return {"discarded": data.draft_id}


class ArchiveIn(BaseModel):
    job_id: str
    archived: bool = True


@operation("job_archive", "application:write", ArchiveIn, "Archive (or restore) an application. Nothing is deleted.", writes=True)
def job_archive(space: UserSpace, ctx: RequestContext, data: ArchiveIn) -> dict:
    space.repo.get_application(data.job_id)
    with space.repo.transaction():
        space.repo.set_archived(data.job_id, data.archived, ctx.actor)
    return {"job_id": data.job_id, "archived": data.archived}


@operation("event_drafts", "application:read", Empty, "Proposed events waiting for the owner.")
def event_drafts(space: UserSpace, ctx: RequestContext, data: Empty) -> list[dict]:
    return [{"draft_id": d["id"], "job_id": d["job_id"], **json.loads(d["data"]), "by": d["created_by"]} for d in space.repo.event_drafts()]
