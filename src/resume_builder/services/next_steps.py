"""What to do next for one job, as copy-ready `rb` commands (issue #36).

Read-only. It looks at where the job is (posting, interest notes, tailored profile, drafts in it,
letters and answers waiting for approval, rendered versions, stage, proposed events) and returns
only the steps that fit, in order. Commands are argument lists (see `commands.py`); the website and
`rb next` format them. It suggests; it never changes a stage or anything else (CLAUDE.md tracker rules).
"""

from __future__ import annotations

import json
from datetime import date

from pydantic import BaseModel

from ..commands import Command, cmd
from ..errors import AppError, NotFound
from ..models import FINAL_STAGES
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace
from .documents import PLACEHOLDER
from .jobs import today_for

MAX_IDS = 5          # list at most this many draft ids or proposed events one by one
DOC_FILES = {"cover_letter": "cover-letter.md"}


class JobIn(BaseModel):
    job_id: str


def _step(key: str, text: str, commands: list[Command] = (), file: str | None = None, prompts: tuple = ()) -> dict:
    """`prompts`: (name, values) for the assistant prompts that do this step (prompts.py, issue #37)."""
    return {"key": key, "text": text, "file": file, "commands": [c.to_dict() for c in commands],
            "prompts": [{"name": name, "values": values} for name, values in prompts]}


def _latest(space: UserSpace, job_id: str, doc_type: str) -> str | None:
    rows = [r for r in space.repo.list_snapshots(job_id, doc_type) if not r["preview"]]
    return rows[-1]["id"] if rows else None


def steps_for(space: UserSpace, job_id: str) -> list[dict]:
    row = space.repo.application_row(job_id)
    if row is None:
        raise NotFound(f"No job `{job_id}`.")
    data = json.loads(row["data"])
    folder = f"jobs/{job_id}"
    steps: list[dict] = []

    drafts = space.repo.event_drafts(job_id)
    if drafts:
        shown = drafts[:MAX_IDS]
        steps.append(_step("event_drafts", f"Your AI agent proposed {len(drafts)} event(s) for this job. Confirm the ones "
                           "that are right and discard the rest (they're listed on this page).",
                           [cmd("confirm-event", str(d["id"])) for d in shown] + [cmd("discard-event", str(d["id"])) for d in shown]))

    if row["archived"]:
        steps.append(_step("archived", "This job is archived. Restore it to see it in your lists again.",
                           [cmd("archive-job", job_id, "--restore")]))
        return steps

    stage = row["stage"]
    if stage in FINAL_STAGES:
        steps.append(_step("closed", f"This application is closed ({stage}). Archive it when you no longer need it in your lists.",
                           [cmd("archive-job", job_id)]))
        return steps

    if stage not in ("interested", "tailoring"):
        return steps + _after_applying(space, job_id)

    # ------------------------------------------------------------ before applying
    url = data.get("posting_url") or ""
    if not (row["posting_text"] or "").strip():
        again = [cmd("new-job", "--recapture", job_id, url, note="Or read it from the link again.")] if url.startswith(("http://", "https://")) else []
        steps.append(_step("posting", "Paste the job posting into posting.md. Tailoring needs it.", again, file=f"{folder}/posting.md"))
    if not (data.get("interest_notes") or "").strip():
        steps.append(_step("interest_notes", "Write why you want this job under interest_notes in application.yaml. "
                           "Cover letters and \"why here\" answers use only these notes for motivation.",
                           file=f"{folder}/application.yaml"))

    profile = space.repo.get_profile(job_id)
    draft_ids: list[str] = []
    if profile is None:
        steps.append(_step("tailor", "Tailor the resume. With a connected assistant, ask it to use the resume-builder tools "
                           f"for job {job_id}. With a chat assistant, paste the brief in and pipe back each block it returns.",
                           [cmd("brief", job_id), cmd("propose", job_id, "profile", clipboard=True),
                            cmd("propose", job_id, "fit_report", clipboard=True)],
                           prompts=(("tailor", {"job_id": job_id}),)))
    else:
        from .render import resolve_resume
        try:
            _, _, draft_ids = resolve_resume(space.repo.load_library(), profile, True)
        except AppError as exc:
            steps.append(_step("profile_invalid", f"The tailored profile doesn't match your content library: {exc.message}",
                               file=f"{folder}/profile.yaml"))
            return steps
        if draft_ids:
            shown = draft_ids[:MAX_IDS]
            more = f" (and {len(draft_ids) - len(shown)} more)" if len(draft_ids) > len(shown) else ""
            steps.append(_step("drafts", f"The tailored resume uses {len(draft_ids)} draft item(s){more}. Check each one's "
                               "text in base.yaml, then confirm it, or preview the resume with the drafts watermarked.",
                               [cmd("confirm", d) for d in shown] + [cmd("render", job_id, "--preview")], file="base.yaml"))

    sent: list[str] = []
    for d in space.repo.list_documents(job_id):
        kind, slug = d["kind"], d["slug"]
        if kind == "fit_report":
            continue
        name = "cover letter" if kind == "cover_letter" else f"answer ({slug})"
        file = f"{folder}/{DOC_FILES.get(kind, f'answers/{slug}.md')}"
        approve = cmd("approve", job_id, kind, *(("--slug", slug) if kind == "answer" else ()))
        if d["status"] == "approved" and d["approved_snapshot"]:
            sid = d["approved_snapshot"]
            sent.append(sid)
            export = [cmd("regenerate", sid)] if kind == "cover_letter" else                 [cmd("regenerate", sid, "--format", "txt", note="Plain text, to paste into an application form.")]
            steps.append(_step(f"export:{kind}:{slug}", f"The {name} is approved ({sid}). Export it to send: it goes into "
                               f"output/, in the {job_id} folder.", export))
        elif PLACEHOLDER in (d["text"] or ""):
            steps.append(_step(f"motivation:{kind}:{slug}", f"The {name} still says {PLACEHOLDER}. Replace it with your own "
                               "words, then approve it.", [approve], file=file))
        else:
            steps.append(_step(f"approve:{kind}:{slug}", f"Read and edit the {name}, then approve it.", [approve], file=file))

    resume = _latest(space, job_id, "resume")
    if profile is not None and not draft_ids and not resume:
        steps.append(_step("render", "Render the tailored resume. Its snapshot id is the version you'll record as sent.",
                           [cmd("render", job_id), cmd("render", job_id, "--ats", note="The plain variant for applicant tracking systems.")]))
    if resume:
        steps.append(_step("applied", "When you've applied, record it with the versions you sent.",
                           [cmd("event", job_id, "--stage", "applied", "--documents", resume, *sent)]))
    return steps


def _after_applying(space: UserSpace, job_id: str) -> list[dict]:
    events = space.repo.event_rows(job_id)
    last = max((date.fromisoformat(e["date"]) for e in events), default=None)
    days = (today_for(space) - last).days if last else None
    record = [cmd("event", job_id, "--type", "message_received", "--notes", "<what happened>"),
              cmd("event", job_id, "--stage", "<stage>", note="screening, interviewing, offer, rejected, withdrawn, ghosted, ...")]
    if days is not None and days >= space.user_settings.tracker.stale_days:
        return [_step("stale", f"No news for {days} days. Follow up, or record what happened. You decide the stage; "
                      "the app never changes it.",
                      [cmd("event", job_id, "--type", "follow_up_sent", "--notes", "<what you sent>"), *record],
                      prompts=(("record-email", {"job_id": job_id}),))]
    return [_step("record", "Record what happens next as it happens. With a connected assistant, paste a recruiter's "
                  "email in and it proposes the event for you to confirm.",
                  [*record, cmd("event", job_id, "--type", "interview", "--notes", "<who and how it went>")],
                  prompts=(("record-email", {"job_id": job_id}),))]


@operation("job_next_steps", "application:read", JobIn,
           "What the user should do next for a job, as copy-ready `rb` commands for the user to run (read-only).")
def job_next_steps(space: UserSpace, ctx: RequestContext, data: JobIn) -> list[dict]:
    return steps_for(space, data.job_id)
