"""Ready-made prompts for the user's AI assistant (issue #37).

The agent can do only a few things, so the useful prompts are a small, known set. They live here once
and are shown on the website's Prompts page, in a job's Next steps, as MCP prompts (`prompts/list`,
`prompts/get`) and by `rb prompts`.

Each prompt has two wordings:

- `connected`: for an assistant that has resume-builder's MCP tools; it names the tools to use.
- `paste`: for a chat assistant, used with copy and paste. `before` is the `rb` command whose output
  the user pastes in first; `after` are the commands that bring each block back. A prompt that needs
  the tools has no paste wording; `instead` is what to run without them.

Prompts ask only for what the agent may already do; the checks in the services decide what's
accepted, so a prompt can't get around them. Nothing here reads user data: callers pass the values.
"""

from __future__ import annotations

from dataclasses import dataclass

from .commands import Command, cmd

CONNECTED, PASTE = "connected", "paste"
MODES = (CONNECTED, PASTE)


@dataclass(frozen=True)
class Arg:
    name: str
    description: str
    placeholder: str


@dataclass(frozen=True)
class Prompt:
    name: str                      # the MCP prompt name, e.g. /mcp__resume-builder__tailor
    title: str
    description: str
    args: tuple[Arg, ...]
    connected: str                 # str.format template over the args
    tools: tuple[str, ...]         # the MCP tools the connected wording names (checked by a test)
    paste: str | None = None
    before: tuple[Command, ...] = ()
    after: tuple[Command, ...] = ()
    instead: tuple[Command, ...] = ()
    group: str = ""


JOB = Arg("job_id", "The job id, e.g. 2026-11-02-example-company", "<job-id>")
LINK = Arg("link", "The posting's link", "<link>")
EMAIL = Arg("email", "The email or message text, pasted in full", "<paste the email here>")
NOTES = Arg("notes", "Your notes about the work: what you did, with what, and what came of it", "<your notes>")
PARENT = Arg("parent_id", "The role or project id in base.yaml the bullets belong to", "<role-id>")
ANSWERS = Arg("answers", "Your answers to the assistant's questions, numbered like its questions", "<your answers>")
LIBRARY = "<paste base.yaml here>"

HONEST = "Use only what's in my content library and my interest notes; don't invent or estimate anything."


def _answer(slug: str, question: str, what: str) -> dict:
    return {
        "connected": (f"Draft {what} for job {{job_id}} and save it with document_propose (kind answer, slug {slug}, "
                      f"question \"{question}\"). Follow the rules in agent_brief: only my tailored profile and my "
                      "interest notes, every claim cited. If my interest notes are empty, write [MOTIVATION NEEDED] "
                      "instead of guessing why I want the job."),
        "paste": (f"Using the brief above, write {what} for job {{job_id}} in one code block, following the brief's "
                  "rules: only my tailored profile and my interest notes, every claim cited. If my interest notes "
                  "are empty, write [MOTIVATION NEEDED] instead of guessing why I want the job."),
    }


PROMPTS: tuple[Prompt, ...] = (
    Prompt(
        "tailor", "Tailor my resume for a job",
        "The tailored profile and fit report for one job, with questions for you.",
        (JOB,),
        "Use the resume-builder tools to tailor my resume for job {job_id}: read agent_brief, then save a tailored "
        "profile with profile_write and a fit report with document_propose (kind fit_report). " + HONEST + " List what "
        "the posting asks for that my library doesn't support as gaps, then ask me your questions, numbered.",
        ("agent_brief", "profile_write", "document_propose"),
        paste="Above is the resume-builder brief for job {job_id}. Following its rules, give me the tailored profile and "
              "the fit report, each in its own code block. " + HONEST + " Then ask me your questions, numbered.",
        before=(cmd("brief", "{job_id}"),),
        after=(cmd("propose", "{job_id}", "profile", clipboard=True), cmd("propose", "{job_id}", "fit_report", clipboard=True)),
        group="Tailor and write"),
    Prompt(
        "redo", "Redo the drafts after answering questions",
        "Give the assistant your answers; it proposes any new facts as draft bullets and updates the drafts.",
        (JOB, ANSWERS),
        "Here are my answers to your questions about job {job_id}:\n\n{answers}\n\nWhere an answer gives a new fact, "
        "propose it as a draft bullet with bullet_propose (I'll check and confirm it). Then update the tailored profile "
        "with profile_write and the fit report with document_propose. Don't use anything I haven't said.",
        ("bullet_propose", "profile_write", "document_propose"),
        paste="Here are my answers to your questions about job {job_id}:\n\n{answers}\n\nWhere an answer gives a new "
              "fact, write it as a bullet in YAML I can paste into base.yaml, with status: draft. Then give me the "
              "updated profile and fit report, each in its own code block. Don't use anything I haven't said.",
        after=(cmd("propose", "{job_id}", "profile", clipboard=True), cmd("propose", "{job_id}", "fit_report", clipboard=True)),
        group="Tailor and write"),
    Prompt(
        "cover-letter", "Draft a cover letter",
        "A 250–350 word letter from the job's tailored profile, every claim cited.",
        (JOB,),
        "Draft a cover letter for job {job_id} and save it with document_propose (kind cover_letter). Follow the rules "
        "in agent_brief: only my tailored profile and my interest notes, every claim cited. If my interest notes are "
        "empty, write [MOTIVATION NEEDED] instead of guessing why I want the job.",
        ("document_propose", "agent_brief"),
        paste="Using the brief above, write the cover letter for job {job_id} in one code block, following the brief's "
              "rules: only my tailored profile and my interest notes, every claim cited. If my interest notes are "
              "empty, write [MOTIVATION NEEDED] instead of guessing why I want the job.",
        before=(cmd("brief", "{job_id}"),),
        after=(cmd("propose", "{job_id}", "cover_letter", clipboard=True),),
        group="Tailor and write"),
    Prompt(
        "why-here", "Draft the \"why do you want to work here\" answer",
        "Up to 100 words: why this role, then your strongest evidence for its top requirements.",
        (JOB,),
        **_answer("why-here", "Why do you want to work here?", "my answer to \"Why do you want to work here?\""),
        tools=("document_propose", "agent_brief"),
        before=(cmd("brief", "{job_id}"),),
        after=(cmd("propose", "{job_id}", "answer", "--slug", "why-here", clipboard=True),),
        group="Tailor and write"),
    Prompt(
        "hiring-team", "Draft a short message to the hiring team",
        "A few lines in place of a cover letter, saved as the hiring-team answer.",
        (JOB,),
        **_answer("hiring-team", "Message to the hiring team", "a short message to the hiring team"),
        tools=("document_propose", "agent_brief"),
        before=(cmd("brief", "{job_id}"),),
        after=(cmd("propose", "{job_id}", "answer", "--slug", "hiring-team", "--question", "Message to the hiring team",
                   clipboard=True),),
        group="Tailor and write"),
    Prompt(
        "add-job", "Add a job from its link",
        "The assistant proposes the job; you review and confirm it.",
        (LINK,),
        "Propose this job posting to resume-builder with job_intake_propose: {link}\n\nOnly transcribe what the "
        "posting says. Before you propose it, ask me how interested I am (1-5), why I want this job, how I found it "
        "and any next step. Pass my answers in from_user exactly as I give them, without rewording, and leave out "
        "anything I skip. I'll review the proposal and confirm it.",
        ("job_intake_propose",),
        instead=(cmd("new-job", "{link}"),),
        group="Jobs and tracking"),
    Prompt(
        "record-email", "Record a recruiter's email",
        "The assistant proposes an event from the email; you confirm it.",
        (JOB, EMAIL),
        "Propose an event for job {job_id} with event_propose, from the email below. Use only what it says: its date, "
        "who it's from and what happened. Don't set a stage; I'll decide that. I'll confirm the event.\n\n{email}",
        ("event_propose",),
        instead=(cmd("event", "{job_id}", "--type", "message_received", "--notes", "<what happened>"),),
        group="Jobs and tracking"),
    Prompt(
        "notes-to-bullets", "Turn notes into resume bullets",
        "Draft bullets for one role or project from your notes; you confirm each one.",
        (PARENT, NOTES),
        "Turn my notes below into resume bullets for {parent_id} and propose each one with bullet_propose (they're "
        "saved as drafts for me to confirm). Lead with the outcome, then the evidence; use the real names of tools. "
        "Use only facts in my notes. Never estimate a number: leave metric empty and ask me instead. Fill in limits "
        "where my notes give them.\n\nNotes:\n{notes}",
        ("bullet_propose",),
        paste="Turn my notes below into resume bullets for {parent_id}, as YAML I can paste into base.yaml under that "
              "role: id, text, tags, metric, evidence, limits, and status: draft. Lead with the outcome, then the "
              "evidence; use the real names of tools. Use only facts in my notes. Never estimate a number: leave "
              "metric as null and ask me instead.\n\nNotes:\n{notes}",
        group="Your content library"),
    Prompt(
        "review-bullets", "Review my bullets",
        "Suggestions against the writing rules. Nothing is saved; you edit base.yaml yourself.",
        (),
        "Read my content library with library_get and review my bullets: outcome first, then evidence; plain, specific "
        "language; real tool names; no filler such as \"proven record\" or \"leveraged\"; no claim broader than the "
        "bullet's limits. List suggested rewrites with each bullet's id. Don't save anything; I'll edit base.yaml myself.",
        ("library_get",),
        paste="Review the bullets in my base.yaml below: outcome first, then evidence; plain, specific language; real "
              "tool names; no filler such as \"proven record\" or \"leveraged\"; no claim broader than the bullet's "
              "limits. List suggested rewrites with each bullet's id. Don't add facts.\n\n" + LIBRARY,
        group="Your content library"),
    Prompt(
        "public-profile", "Update my public resume",
        "Pick the confirmed bullets for your general resume; older roles condensed, not dropped.",
        (),
        "Read my content library with library_get and update my public resume with profile_write (key public): pick "
        "the confirmed summary phrases and bullets that best show my strengths overall. Condense older roles into "
        "earlier_roles instead of leaving them out. Ids only; no new text.",
        ("library_get", "profile_write"),
        paste="From my base.yaml below, give me a public resume profile as YAML in one code block: ids only, no new "
              "text, only confirmed items, older roles condensed into earlier_roles instead of left out.\n\n" + LIBRARY,
        after=(cmd("propose", "public", "profile", clipboard=True),),
        group="Your content library"),
)

BY_NAME = {p.name: p for p in PROMPTS}


class MissingArgument(ValueError):
    pass


def text(p: Prompt, mode: str, values: dict | None = None, strict: bool = False) -> str | None:
    """The prompt's text in one wording, with values filled in. Unknown values stay as placeholders,
    unless `strict` (MCP), where a missing one is an error. None if it has no wording for `mode`."""
    template = p.connected if mode == CONNECTED else p.paste
    if template is None:
        return None
    filled = {}
    for a in p.args:
        v = (values or {}).get(a.name)
        if not (isinstance(v, str) and v.strip()):
            if strict:
                raise MissingArgument(f"The `{p.name}` prompt needs `{a.name}`: {a.description}.")
            v = a.placeholder
        filled[a.name] = v.strip()
    return template.format(**filled)


def commands(cs: tuple[Command, ...], values: dict | None = None) -> list[Command]:
    """Commands with `{job_id}`-style values filled in (placeholders where unknown)."""
    out = []
    for c in cs:
        argv = []
        for a in c.argv:
            for arg in (JOB, LINK):
                v = (values or {}).get(arg.name)
                a = a.replace("{" + arg.name + "}", v if isinstance(v, str) and v.strip() else arg.placeholder)
            argv.append(a)
        out.append(Command(tuple(argv), c.note, c.stdin_file, c.clipboard))
    return out


def preferred_mode(agent_state: str | None) -> str:
    """Connected wording once an assistant has reached the app directly; copy and paste otherwise."""
    return CONNECTED if agent_state == "connected" else PASTE


def mcp_list() -> list[dict]:
    return [{"name": p.name, "title": p.title, "description": p.description,
             "arguments": [{"name": a.name, "description": a.description, "required": True} for a in p.args]}
            for p in PROMPTS]


def mcp_get(name: str, arguments: dict | None) -> dict:
    p = BY_NAME.get(name)
    if p is None:
        raise KeyError(f"Unknown prompt `{name}`. Prompts: {', '.join(BY_NAME)}.")
    body = text(p, CONNECTED, arguments, strict=True)
    return {"description": p.description, "messages": [{"role": "user", "content": {"type": "text", "text": body}}]}
