"""Profiles (selections of content-library ids) and the agent brief (PRD 8.3, section 7)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..errors import NotFound, ValidationFailed
from ..models import Profile
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace
from ..validation import validate_profile


class ProfileKeyIn(BaseModel):
    key: str = Field(description="`public` or a job id")


@operation("profile_get", "profile:read", ProfileKeyIn, "A profile: the selected ids and their order.")
def profile_get(space: UserSpace, ctx: RequestContext, data: ProfileKeyIn) -> dict:
    p = space.repo.get_profile(data.key)
    if p is None:
        raise NotFound(f"No profile for `{data.key}` yet.")
    return p.model_dump(mode="json")


class ProfileWriteIn(BaseModel):
    key: str
    profile: Profile


@operation("profile_write", "profile:write", ProfileWriteIn, "Save a profile (ids only; validated against the content library).", writes=True)
def profile_write(space: UserSpace, ctx: RequestContext, data: ProfileWriteIn) -> dict:
    if data.key != "public" and not space.repo.application_row(data.key):
        raise NotFound(f"No job `{data.key}`.")
    rel = "profiles/public.yaml" if data.key == "public" else f"jobs/{data.key}/profile.yaml"
    problems = validate_profile(data.profile, space.repo.load_library(), rel, space.repo.archived_ids())
    if any(p.severity == "error" for p in problems):
        raise ValidationFailed("The profile doesn't match the content library.", problems)
    with space.repo.transaction():
        space.repo.save_profile(data.key, data.profile, ctx.actor)
    written = space.changed("profile", data.key)
    _flag_outdated_documents(space, data.key)
    return {"saved": data.key, "warnings": [p.to_dict() for p in problems if p.severity == "warning"], "file_updated": written}


def _flag_outdated_documents(space: UserSpace, job_id: str) -> None:
    """R9.14: approved letters/answers are out of date when the profile changes after approval."""
    if job_id == "public":
        return
    for row in space.repo.list_documents(job_id):
        if row["status"] == "approved" and row["kind"] != "fit_report":
            warnings = [{"message": "The tailored profile changed after this was approved. Review it and approve again.", "severity": "warning"}]
            with space.repo.transaction():
                space.repo.save_document(job_id, row["kind"], row["slug"], row["text"], "draft", "system",
                                         warnings=warnings, approved_snapshot=row["approved_snapshot"])
            space.changed("document", job_id, doc_kind=row["kind"], slug=row["slug"])


AGENT_RULES = """\
You are helping a job seeker tailor application materials with resume-builder. You select and
order content they already have; you never write new claims about them.

Rules (non-negotiable; the app rejects drafts that break them):
- Never invent experience, tools, metrics, dates, titles or outcomes. Use only confirmed items from
  the user's content library, referenced by id.
- A profile lists ids only, in order. No new resume text. Roles newest first. Put recent, relevant
  roles under `roles`. Condense older or less relevant roles under `earlier_roles` (role id -> [] or a
  few bullet ids) instead of dropping them, so the work history has no gap. Use `omitted_roles`, with
  a reason, only when the user asks to leave a role out. `keywords` must be tags on the user's bullets.
- Respect each bullet's `limits`. Never estimate a metric; if one is missing, ask in the fit report.
- Anything the posting asks for that the library doesn't support goes under `gaps` in the profile,
  as a question for the user ("real gap, or missing information?"). Never fill a gap.
- Cover letters and answers draw only on the bullets selected in that job's profile. Every sentence
  that makes a claim about the candidate ends with a citation: `[cite: bullet-id, other-id]`. Every
  number and tool in that sentence must appear in a cited bullet.
- Motivation ("why I want this job") comes only from the user's interest notes. If they are empty,
  write `[MOTIVATION NEEDED]` instead of inventing enthusiasm. Company facts come only from the posting.
- Plain, specific language. No corporate filler.
- Everything you produce is a draft. The user reviews and approves it. You cannot confirm content,
  approve documents, record events (only propose them) or change companies.

Fit report format (Markdown):
1. Strong matches: each posting requirement, then the bullet ids that support it.
2. Weak spots: partial matches, and what is missing.
3. Gaps: requirements with no support in the library.
4. Questions for the user: missing metrics, unclear claims.
5. Notes on the role: what the posting suggests about the actual work, office arrangement and seniority.
"""


def agent_instructions(mode: str = "chat") -> str:
    """The standing instructions for any AI tool: rules plus how to hand work back."""
    return AGENT_RULES + "\n" + _HOW_TO_SUBMIT[mode]("<user>", "<job-id>")


_HOW_TO_SUBMIT = {
    "mcp": lambda user, job: f"""\
How to work (resume-builder MCP tools):
1. Call `agent_brief` with the job id to get the posting, interest notes and confirmed content.
2. Call `profile_write` with key "{job}" and the profile object.
3. Call `document_propose` with kind "fit_report" and the Markdown report.
4. Only if the user asks: `document_propose` with kind "cover_letter", or kind "answer" with
   slug "why-here" and question "Why do you want to work here?".
5. If a tool returns problems, fix exactly what they say and call it again.
6. For missing content the user confirms exists, `bullet_propose` saves it as a draft for them.
""",
    "api": lambda user, job: f"""\
How to submit (HTTP API at http://localhost:8000, header `Authorization: Bearer <agent token>`):
- Profile: POST /api/{user}/profile_write  {{"key": "{job}", "profile": {{...}}}}
- Fit report: POST /api/{user}/document_propose  {{"job_id": "{job}", "kind": "fit_report", "text": "..."}}
- Cover letter: the same with "kind": "cover_letter". Answer: "kind": "answer", "slug": "why-here",
  "question": "Why do you want to work here?"
- Missing content: POST /api/{user}/bullet_propose (saved as a draft for the user to confirm).
- Schemas for every operation: GET /openapi.json. A 422 response lists the problems to fix.
""",
    "chat": lambda user, job: f"""\
How to hand your work back (the user pastes each block into resume-builder):
Reply with up to four fenced code blocks, each preceded by its heading, and nothing inside the
blocks except the content:

### profile.yaml
```yaml
summary: [<summary phrase ids>]
roles:
  <role-id>: [<bullet ids, best first>]
projects:
  <project-id>: [<bullet ids>]
education: [<ids>]
certifications: [<ids>]
skills: [<skill group ids, most relevant first>]
keywords: [<posting keywords that are tags on the selected bullets>]
earlier_roles:
  <older-role-id>: [<bullet ids, often none>]
omitted_roles: []   # only roles the user asked to leave out: - role: <role-id>, reason: <why>
gaps:
  - skill: <what the posting asks for>
    note: <question for the user>
```

### fit-report.md
```markdown
(the five-part fit report)
```

### cover-letter.md   (only if asked)
### why-here.md       (only if asked)

The user saves them with `rb propose {job} profile`, `... fit_report`, `... cover_letter` or
`... answer`. If the app reports problems, the user will paste them back to you: fix exactly those.
""",
}


class BriefIn(BaseModel):
    job_id: str
    mode: str = Field("chat", pattern="^(chat|mcp|api)$", description="How the agent hands work back")


@operation("agent_brief", "content:read", BriefIn, "Everything an AI agent needs to tailor for a job, with the rules.")
def agent_brief(space: UserSpace, ctx: RequestContext, data: BriefIn) -> str:
    row = space.repo.application_row(data.job_id)
    if not row:
        raise NotFound(f"No job `{data.job_id}`.")
    app = space.repo.get_application(data.job_id)
    lib = space.repo.load_library()
    s = space.user_settings
    lines = [
        f"# Agent brief: {data.job_id}",
        "",
        AGENT_RULES,
        "This user's limits:",
        f"- Answers: at most {s.writing.answer_max_words} words. Cover letters: {s.writing.cover_letter_min_words}–{s.writing.cover_letter_max_words} words.",
        "- Banned phrases: " + ", ".join(f'"{p}"' for p in s.writing.banned_phrases) + ".",
        "",
        _HOW_TO_SUBMIT[data.mode](space.user, data.job_id),
        "## Job",
        f"- Company: {app.company}" + (f" / {app.department}" if app.department else ""),
        f"- Title: {app.title or '(not set)'}",
        f"- Interest notes (the only source of motivation): {app.interest_notes or '(empty — ask the user)'}",
        "",
        "### Posting",
        "",
        (row["posting_text"] or "(The posting hasn't been pasted into posting.md yet.)").strip(),
        "",
        "## Confirmed content library",
        "",
    ]
    for sp in lib.summary_phrases:
        if sp.status == "confirmed":
            lines.append(f"- summary `{sp.id}` [{', '.join(sp.tags)}]: {sp.text}")
    for group, label in ((lib.roles, "role"), (lib.projects, "project")):
        for item in group:
            name = getattr(item, "title", None) or getattr(item, "name", "")
            where = f" at {item.company}" if hasattr(item, "company") else ""
            lines.append(f"\n### {label} `{item.id}`: {name}{where} ({item.start or '?'} – {item.end or '?'})")
            for b in item.bullets:
                if b.status != "confirmed":
                    continue
                extra = []
                if b.metric:
                    extra.append(f"metric: {b.metric}")
                if b.limits:
                    extra.append(f"limits: {b.limits}")
                lines.append(f"- `{b.id}` [{', '.join(b.tags)}] {b.text.replace('**', '')}" + (f" ({'; '.join(extra)})" if extra else ""))
    if lib.skills:
        lines.append("\n### Skill groups")
        for g in lib.skills:
            lines.append(f"- `{g.id}` {g.name}: {', '.join(g.items)}")
    other = [e for e in lib.education if e.status == "confirmed"] + [c for c in lib.certifications if c.status == "confirmed"]
    if other:
        lines.append("\n### Education and certifications")
        for e in other:
            lines.append(f"- `{e.id}` {getattr(e, 'institution', None) or getattr(e, 'name', '')}")
    drafts = [b.id for b in lib.all_bullets().values() if b.status != "confirmed"]
    if drafts:
        lines.append(f"\n(Not usable until the user confirms them: {', '.join(drafts)})")
    return "\n".join(lines) + "\n"
