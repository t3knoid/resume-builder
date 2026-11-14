"""Dashboard, upcoming actions and company view (PRD 8.8)."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import date

from pydantic import BaseModel

from ..models import STAGES
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace
from .jobs import ListIn, job_list, today_for

RESPONSE_TYPES = {"message_received", "interview"}
RESPONSE_STAGES = {"acknowledged", "screening", "interviewing", "offer", "accepted", "declined", "rejected"}


class Empty(BaseModel):
    pass


@operation("dashboard", "report:read", Empty, "Search statistics: volume, response rate, time to response, conversion.")
def dashboard(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    events = defaultdict(list)
    for e in space.repo.all_events():
        events[e["job_id"]].append(e)
    rows = {r["job_id"]: r for r in space.repo.list_application_rows()}
    per_week: Counter = Counter()
    applied, responded, days_to_response = 0, 0, []
    reached: Counter = Counter()
    by_source: dict[str, Counter] = defaultdict(Counter)
    letter = {"with": [0, 0], "without": [0, 0]}  # [applied, responded]
    interview_bullets: Counter = Counter()
    for job_id, row in rows.items():
        evs = events.get(job_id, [])
        data_ = json.loads(row["data"])
        stages_seen = {e["stage"] for e in evs if e["stage"]}
        for s in stages_seen:
            reached[s] += 1
        applied_ev = next((e for e in evs if e["stage"] == "applied"), None)
        source = data_.get("source") or "unknown"
        by_source[source][row["stage"]] += 1
        if not applied_ev:
            continue
        applied += 1
        d0 = date.fromisoformat(applied_ev["date"])
        per_week[d0.strftime("%G-W%V")] += 1
        resp = next((e for e in evs if e["seq"] > applied_ev["seq"] and (e["type"] in RESPONSE_TYPES or e["stage"] in RESPONSE_STAGES)), None)
        has_letter = bool(data_.get("submission", {}).get("cover_letter_snapshot"))
        bucket = letter["with" if has_letter else "without"]
        bucket[0] += 1
        if resp:
            responded += 1
            bucket[1] += 1
            days_to_response.append((date.fromisoformat(resp["date"]) - d0).days)
        if stages_seen & {"interviewing", "offer", "accepted", "declined"}:
            sid = data_.get("submission", {}).get("resume_snapshot")
            if sid:
                interview_bullets.update(space.repo.snapshot_bullets(sid))
    funnel = [{"stage": s, "count": reached.get(s, 0)} for s in STAGES if reached.get(s)]
    return {
        "applications": len(rows),
        "applied": applied,
        "applications_per_week": dict(sorted(per_week.items())),
        "response_rate": round(responded / applied, 3) if applied else None,
        "median_days_to_first_response": statistics.median(days_to_response) if days_to_response else None,
        "stages_reached": funnel,
        "outcomes_by_source": {k: dict(v) for k, v in by_source.items()},
        "response_rate_with_cover_letter": round(letter["with"][1] / letter["with"][0], 3) if letter["with"][0] else None,
        "response_rate_without_cover_letter": round(letter["without"][1] / letter["without"][0], 3) if letter["without"][0] else None,
        "bullets_in_resumes_that_reached_interviews": interview_bullets.most_common(15),
    }


@operation("actions", "report:read", Empty, "Next actions by due date, and stale applications.")
def actions(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    apps = job_list(space, ctx, ListIn())
    upcoming = sorted([a for a in apps if a["next_action"]], key=lambda a: a["next_action_due"] or "9999")
    today = today_for(space).isoformat()
    for a in upcoming:
        a["overdue"] = bool(a["next_action_due"] and a["next_action_due"] < today)
    return {"upcoming": upcoming, "stale": [a for a in apps if a["stale"]],
            "event_drafts": len(space.repo.event_drafts())}


class CompanyIn(BaseModel):
    company: str


@operation("company_view", "report:read", CompanyIn, "Every application to one company over time.")
def company_view(space: UserSpace, ctx: RequestContext, data: CompanyIn) -> dict:
    reg = {c.slug: c for c in space.repo.load_registry(include_archived=True).companies}
    comp = reg.get(data.company)
    apps = job_list(space, ctx, ListIn(include_archived=True, company=data.company))
    return {"company": comp.model_dump(mode="json") if comp else {"slug": data.company},
            "applications": sorted(apps, key=lambda a: a["job_id"])}
