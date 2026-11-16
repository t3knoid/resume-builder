"""Content rules (PRD 8.4), lifecycle rules (8.6) and registry rules (8.11).

The same checks run for YAML sync and for API writes (A6).
"""

from __future__ import annotations

from collections import Counter

from pydantic import ValidationError

from .errors import Problem
from .models import (
    FINAL_STAGES, OPEN_STAGES, STAGES, Application, ContentLibrary, Event, OrganizationRegistry, Profile,
)

_EXAMPLES = {
    "id": "aws-cost-cut",
    "status": "confirmed",
    "stage": "applied",
    "type": "stage_change",
    "date": "2026-11-13",
    "start": "2022-07",
    "end": "present",
    "company": "acme-corp",
    "slug": "acme-corp",
    "tags": "[python, ansible]",
    "work_arrangement": "remote",
    "source": "linkedin",
    "role": "recruiter",
    "interest_level": "4",
}


def pydantic_problems(exc: ValidationError, rel: str, doc=None, line_of=None) -> list[Problem]:
    out = []
    for err in exc.errors():
        loc = tuple(err.get("loc", ()))
        field = ".".join(str(p) for p in loc) or None
        last = next((str(p) for p in reversed(loc) if isinstance(p, str)), None)
        msg = err.get("msg", "invalid value")
        etype = err.get("type", "")
        if etype == "extra_forbidden":
            msg = f"`{last}` is not a known field. Check the spelling, or delete it."
        elif etype == "missing":
            msg = f"`{last}` is required."
        elif etype in ("literal_error", "enum"):
            expected = err.get("ctx", {}).get("expected", "")
            msg = f"Not an allowed value. Use one of: {expected}."
        elif etype == "string_pattern_mismatch":
            msg = "Wrong format."
        elif msg.startswith("Value error, "):
            msg = msg[len("Value error, "):].rstrip(".") + "."
        line = line_of(doc, loc) if (doc is not None and line_of) else None
        out.append(Problem(msg, file=rel, line=line, field=field, example=_EXAMPLES.get(last or "")))
    return out


# ---------------------------------------------------------------- library

def validate_library(lib: ContentLibrary, rel: str = "base.yaml") -> list[Problem]:
    problems = []
    counts = Counter(lib.all_ids())
    for item_id, n in counts.items():
        if n > 1:
            problems.append(Problem(f"Id `{item_id}` is used {n} times. Every id must be unique.", file=rel, field="id", example="aws-cost-cut-2"))
    for bid, b in lib.all_bullets().items():
        if b.text.count("**") % 2:
            problems.append(Problem(
                f"Bullet `{bid}` has an unclosed `**`. Bold text needs a pair: one `**` before and one after.",
                file=rel, field=f"{bid}.text", example="Cut release time by moving builds to **GitHub Actions**."))
    return problems


def validate_registry(reg: OrganizationRegistry, rel: str = "organizations.yaml") -> list[Problem]:
    problems = []
    for slug, n in Counter(c.slug for c in reg.companies).items():
        if n > 1:
            problems.append(Problem(f"Company slug `{slug}` appears {n} times.", file=rel, field="slug"))
    for c in reg.companies:
        for slug, n in Counter(d.slug for d in c.departments).items():
            if n > 1:
                problems.append(Problem(f"Department `{slug}` appears {n} times under `{c.slug}`.", file=rel, field="departments"))
    return problems


# ---------------------------------------------------------------- profiles

def roles_newest_first(profile: Profile, lib: ContentLibrary) -> Profile:
    """The profile with `roles` and `earlier_roles` in the order of the user's history, newest first (R4.4).

    The library knows each role's dates, so the app sets this order itself instead of trusting the
    order of a mapping's keys, which JSON doesn't guarantee (some clients sort them). Roles with the
    same start keep the order they were given; ids not in the library stay last, for the check to report.
    """
    starts = {r.id: r.start for r in lib.roles}

    def ordered(group: dict) -> dict:
        known = sorted((r for r in group if r in starts), key=lambda r: starts[r], reverse=True)
        return {r: group[r] for r in known + [r for r in group if r not in starts]}

    roles, earlier = ordered(profile.roles), ordered(profile.earlier_roles)
    if list(roles) == list(profile.roles) and list(earlier) == list(profile.earlier_roles):
        return profile
    return profile.model_copy(update={"roles": roles, "earlier_roles": earlier})


def validate_profile(profile: Profile, lib: ContentLibrary, rel: str, archived: set[str] | None = None) -> list[Problem]:
    """R4.1–R4.4. Referencing a draft is a warning here and an error at final render (R2.3)."""
    problems: list[Problem] = []
    archived = archived or set()
    bullets = lib.all_bullets()
    roles = {r.id: r for r in lib.roles}
    projects = {p.id: p for p in lib.projects}
    phrases = {s.id: s for s in lib.summary_phrases}
    edu = {e.id: e for e in lib.education}
    certs = {c.id: c for c in lib.certifications}
    skills = {g.id: g for g in lib.skills}

    def missing(item_id: str, what: str, field: str):
        hint = " It was removed from base.yaml (archived)." if item_id in archived else ""
        problems.append(Problem(f"{what} `{item_id}` is not in your content library.{hint}", file=rel, field=field))

    def draft(item_id: str, what: str, field: str):
        problems.append(Problem(
            f"{what} `{item_id}` is still a draft, so only a preview can include it. Set `status: confirmed` in base.yaml once it's accurate.",
            file=rel, field=field, severity="warning"))

    for sid in profile.summary:
        if sid not in phrases:
            missing(sid, "Summary phrase", "summary")
        elif phrases[sid].status != "confirmed":
            draft(sid, "Summary phrase", "summary")
    for group, table, label in ((profile.roles, roles, "roles"), (profile.projects, projects, "projects")):
        for parent_id, bullet_ids in group.items():
            if parent_id not in table:
                missing(parent_id, label[:-1].capitalize(), label)
                continue
            own = {b.id for b in table[parent_id].bullets}
            for bid in bullet_ids:
                if bid not in bullets:
                    missing(bid, "Bullet", f"{label}.{parent_id}")
                elif bid not in own:
                    problems.append(Problem(f"Bullet `{bid}` belongs to a different {label[:-1]}, not `{parent_id}`.", file=rel, field=f"{label}.{parent_id}"))
                elif bullets[bid].status != "confirmed":
                    draft(bid, "Bullet", f"{label}.{parent_id}")
    for ids, table, label in ((profile.education, edu, "education"), (profile.certifications, certs, "certifications")):
        for item_id in ids:
            if item_id not in table:
                missing(item_id, label.capitalize(), label)
            elif table[item_id].status != "confirmed":
                draft(item_id, label.capitalize(), label)
    for gid in profile.skills:
        if gid not in skills:
            missing(gid, "Skill group", "skills")
        elif skills[gid].status != "confirmed":
            draft(gid, "Skill group", "skills")

    tags = lib.all_tags()
    for kw in profile.keywords:
        if kw not in tags:
            problems.append(Problem(f"Keyword `{kw}` is not a tag on any bullet, so the profile can't claim it. Add it as a gap instead.", file=rel, field="keywords"))

    # Earlier roles: condensed into one entry, so they still count as shown.
    for role_id, bullet_ids in profile.earlier_roles.items():
        if role_id not in roles:
            missing(role_id, "Role", "earlier_roles")
            continue
        if role_id in profile.roles:
            problems.append(Problem(f"Role `{role_id}` is under both `roles` and `earlier_roles`. Pick one.", file=rel, field="earlier_roles"))
        own = {b.id for b in roles[role_id].bullets}
        for bid in bullet_ids:
            if bid not in bullets:
                missing(bid, "Bullet", f"earlier_roles.{role_id}")
            elif bid not in own:
                problems.append(Problem(f"Bullet `{bid}` belongs to a different role, not `{role_id}`.", file=rel, field=f"earlier_roles.{role_id}"))
            elif bullets[bid].status != "confirmed":
                draft(bid, "Bullet", f"earlier_roles.{role_id}")

    # R4.4: the app puts roles newest first itself (roles_newest_first); none may be dropped silently.
    if profile.roles and profile.earlier_roles:
        newest_earlier = max((roles[r].start for r in profile.earlier_roles if r in roles), default="")
        oldest_main = min((roles[r].start for r in profile.roles if r in roles), default="9999")
        if newest_earlier > oldest_main:
            problems.append(Problem("Every role under `earlier_roles` must be older than the roles under `roles`; the earlier entry is shown after them.",
                                    file=rel, field="earlier_roles"))
    omitted = {o.role for o in profile.omitted_roles}
    for r in lib.roles:
        if r.id not in profile.roles and r.id not in profile.earlier_roles and r.id not in omitted:
            problems.append(Problem(
                f"Role `{r.id}` is left out without a reason. Add it under `roles`, condense it under `earlier_roles`, or list it in `omitted_roles` with a reason, to avoid an unexplained gap.",
                file=rel, field="omitted_roles", example=f"earlier_roles:\n  {r.id}: []"))
    return problems


# ---------------------------------------------------------------- applications

def stage_after(events: list[Event]) -> tuple[str, list[str]]:
    """Walk the event log; return the current stage and any transition errors (section 8.6)."""
    stage = None
    errors: list[str] = []
    for i, ev in enumerate(events, start=1):
        if ev.type == "correction":
            if ev.corrects and ev.corrects >= i:
                errors.append(f"Event {i}: `corrects` must point to an earlier event (1 to {i - 1}).")
            if ev.stage:
                stage = ev.stage
            continue
        if ev.type != "stage_change":
            continue
        new = ev.stage
        if stage is None:
            if new != "interested":
                errors.append(f"Event {i}: the first stage must be `interested`.")
            stage = new
            continue
        if not transition_allowed(stage, new):
            errors.append(f"Event {i}: can't move from `{stage}` to `{new}`. {transition_hint(stage)}")
            continue
        stage = new
    return stage or "interested", errors


def transition_allowed(old: str, new: str) -> bool:
    if old in FINAL_STAGES:
        return False
    if new == "withdrawn":
        return True
    if new in ("rejected", "ghosted"):
        return STAGES.index(old) >= STAGES.index("applied")
    if new in ("accepted", "declined"):
        return old == "offer"
    if new in OPEN_STAGES:
        return STAGES.index(new) > STAGES.index(old)
    return False


def transition_hint(old: str) -> str:
    if old in FINAL_STAGES:
        return f"`{old}` is final; add a `correction` event if it was recorded by mistake."
    allowed = [s for s in STAGES if transition_allowed(old, s)]
    return "Allowed next: " + ", ".join(f"`{s}`" for s in allowed) + "."


def validate_application(app: Application, reg: OrganizationRegistry, rel: str) -> list[Problem]:
    problems: list[Problem] = []
    companies = {c.slug: c for c in reg.companies}
    if app.company not in companies:
        problems.append(Problem(
            f"Company `{app.company}` is not in organizations.yaml. Add it there, or use an existing slug.",
            file=rel, field="company", example=next(iter(companies), "acme-corp")))
    elif app.department and app.department not in {d.slug for d in companies[app.company].departments}:
        problems.append(Problem(
            f"Department `{app.department}` is not listed under `{app.company}` in organizations.yaml.",
            file=rel, field="department"))
    dates = [e.date for e in app.events]
    for i in range(1, len(dates)):
        if dates[i] < dates[i - 1]:
            problems.append(Problem(f"Event {i + 1} is dated before event {i}. Events must be in date order.", file=rel, field=f"events.{i}.date"))
    _, errors = stage_after(app.events)
    for e in errors:
        problems.append(Problem(e, file=rel, field="events"))
    return problems
