"""Copy-ready `rb` commands for people (issue #36).

A command is kept as an argument list (without `rb` and `--user`), plus what it reads on stdin, so a
test can parse every one with the real `rb` parser and the website can't drift from the CLI. The
website and `rb next` turn them into text for each shell. Nothing here reads user data: callers pass
in the values (job id, snapshot id) to fill in. Values the app can't know stay as `<placeholders>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SH = "macOS, Linux"
PS = "Windows PowerShell"
SAFE = re.compile(r"^[A-Za-z0-9_@%+=:,./<>-]+$")


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    note: str = ""
    stdin_file: str | None = None      # piped from a file: `< posting.txt` / `Get-Content posting.txt |`
    clipboard: bool = False            # piped from the clipboard: `pbpaste |` / `Get-Clipboard |`

    def to_dict(self) -> dict:
        return {"argv": list(self.argv), "note": self.note, "stdin_file": self.stdin_file, "clipboard": self.clipboard}

    @classmethod
    def from_dict(cls, d: dict) -> "Command":
        return cls(tuple(d["argv"]), d.get("note", ""), d.get("stdin_file"), bool(d.get("clipboard")))


def cmd(*argv: str, note: str = "", stdin_file: str | None = None, clipboard: bool = False) -> Command:
    return Command(tuple(argv), note, stdin_file, clipboard)


def quote(arg: str) -> str:
    """Quote for both sh and PowerShell: plain words as they are, anything else in double quotes.

    The shells escape differently inside double quotes, so characters they treat specially there
    (`"`, `$`, backtick, backslash) are dropped; commands here never need them.
    """
    if SAFE.match(arg):
        return arg
    return '"' + re.sub(r'["`$\\]', "", arg) + '"'


def lines(c: Command, user: str | None = None) -> list[dict]:
    """The command as text, one entry per shell where they differ: [{"label": ..., "text": ...}]."""
    words = ["rb"] + (["--user", user] if user else []) + [quote(a) for a in c.argv]
    base = " ".join(words)
    if c.clipboard:
        return [{"label": "macOS", "text": f"pbpaste | {base}"}, {"label": PS, "text": f"Get-Clipboard | {base}"}]
    if c.stdin_file:
        return [{"label": SH, "text": f"{base} < {c.stdin_file}"},
                {"label": PS, "text": f"Get-Content {c.stdin_file} | {base}"}]
    return [{"label": "", "text": base}]


def is_placeholder(arg: str) -> bool:
    return "<" in arg and ">" in arg


# ---------------------------------------------------------------- the Commands page

def catalog(job: str | None = None, snapshot: str | None = None, letter: str | None = None) -> list[dict]:
    """Every everyday command, grouped by task in the order you use them, with the values filled in.

    `snapshot` is the job's latest resume snapshot, `letter` its approved cover letter's snapshot.
    """
    j = job or "<job-id>"
    s = snapshot or "<snapshot-id>"
    l = letter or "<snapshot-id>"
    return [
        {"title": "Check and sync", "commands": [
            cmd("status", note="Sync problems, example entries left, and whether your AI agent is connected."),
            cmd("sync", note="Apply your file edits now. The app also does this by itself within seconds."),
            cmd("confirm", "--all", note="Confirm every draft item in base.yaml (it lists them and asks first)."),
            cmd("render", "public", "--preview", note="Preview your general resume, drafts included and watermarked."),
            cmd("render", "public", note="Your general resume, confirmed items only."),
        ]},
        {"title": "Add a job", "commands": [
            cmd("new-job", "<link>", note="From the posting's link: reads it, shows what it found, and asks once."),
            cmd("new-job", "--file", "intake/<name>.txt", "<link>",
                note="The site needs a sign-in: save the posting text in your intake folder first."),
            cmd("new-job", "--stdin", "<link>", stdin_file="posting.txt",
                note="The same, piping the posting text in (shows the preview; add --yes to create)."),
            cmd("new-job", "--title", "<job title>", note="No link: asks for the company, then paste the posting into posting.md."),
            cmd("job-drafts", note="Jobs your AI agent proposed, waiting for you."),
        ]},
        {"title": "Tailor with an AI assistant", "commands": [
            cmd("brief", j, note="Copy and paste: give this to a chat assistant (claude.ai, ChatGPT, Gemini, Copilot)."),
            cmd("propose", j, "profile", clipboard=True, note="Copy the profile block it gives back, then run this."),
            cmd("propose", j, "fit_report", clipboard=True, note="The fit report block."),
            cmd("propose", j, "cover_letter", clipboard=True, note="The cover letter block."),
            cmd("propose", j, "answer", "--slug", "why-here", clipboard=True, note='The "Why do you want this role?" answer.'),
            cmd("propose", j, "answer", "--slug", "hiring-team", "--question", "Message to the hiring team", clipboard=True,
                note="A short message to the hiring team instead of a full cover letter."),
            cmd("agent-instructions", note="Standing instructions to paste into the assistant's custom instructions once."),
            cmd("token", "--actor", "agent", note="An agent token, for an assistant connected by URL or the HTTP API."),
        ]},
        {"title": "Render the resume", "commands": [
            cmd("render", j, "--preview", note="The tailored resume, drafts included and watermarked."),
            cmd("render", j, note="The tailored resume as a PDF, ready to send. Its snapshot id is the version you record."),
            cmd("render", j, "--ats", note="The plain single-column variant for applicant tracking systems."),
            cmd("render", j, "--format", "html", note="The tailored resume as a web page instead of a PDF."),
            cmd("snapshots", j, note="Every saved version of this job's documents, with their ids."),
            cmd("regenerate", s, note="The latest resume version, rebuilt exactly (the same PDF), into output/."),
        ]},
        {"title": "Cover letter and answers", "commands": [
            cmd("approve", j, "cover_letter", note="Approve the cover letter once you've read and edited it."),
            cmd("render", j, "--type", "cover_letter", note="A PDF of the cover letter as it is now, into output/."),
            cmd("regenerate", l, note="The approved cover letter's PDF, rebuilt exactly, into output/."),
            cmd("approve", j, "answer", "--slug", "why-here", note="Approve the answer."),
            cmd("render", j, "--type", "answer", "--slug", "why-here", "--format", "txt",
                note="The answer as plain text, to paste into an application form."),
            cmd("approve", j, "answer", "--slug", "hiring-team", note="Approve the message to the hiring team."),
            cmd("render", j, "--type", "answer", "--slug", "hiring-team", "--format", "txt",
                note="The message as plain text, to paste into the application form."),
        ]},
        {"title": "Track the application", "commands": [
            cmd("event", j, "--stage", "applied", "--documents", s, note="You applied: record it with the versions you sent."),
            cmd("event", j, "--type", "message_received", "--notes", "<what happened>", note="A reply, an email, a call."),
            cmd("event", j, "--type", "interview", "--notes", "<who and how it went>", note="An interview."),
            cmd("event", j, "--type", "follow_up_sent", "--notes", "<what you sent>", note="You followed up."),
            cmd("event", j, "--stage", "<stage>", note="The stage changed (screening, interviewing, offer, rejected, ...)."),
            cmd("set", j, "--interest-notes", "<why you want this job>",
                note="Why you want the job, in your words (letters use only these notes for motivation). "
                     "Add --interest-level 1 to 5 too."),
            cmd("set", j, "--next-action", "<what to do>", "--due", "<YYYY-MM-DD>", note="Your next step and when it's due."),
            cmd("event-drafts", note="Events your AI agent proposed, waiting for you."),
            cmd("update-drafts", note="Changes to job fields your AI agent proposed, waiting for you."),
            cmd("actions", note="Upcoming actions and stale applications."),
            cmd("next", j, note="What to do next for this job."),
            cmd("archive-job", j, note="Archive the job (nothing is deleted)."),
        ]},
        {"title": "Website, backups and checks", "commands": [
            cmd("web-login", note="A one-time link that signs you in to this website."),
            cmd("backup", note="Back up the database now."),
            cmd("backups", note="List backups."),
            cmd("restore", "<backup-file>", note="Restore the database from a backup."),
            cmd("doctor", note="Check everything without changing anything."),
        ]},
    ]
