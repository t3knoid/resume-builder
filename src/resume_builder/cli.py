"""`rb`: the command-line interface. A thin client of the operation registry (PRD A5, DK12).

Every command that touches data calls an operation through `invoke`, so it gets the same
permission checks, validation and audit as the HTTP API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

from . import services, sync_ops  # noqa: F401  (registers operations)
from . import sync as sync_mod
from .errors import AppError
from .operations import invoke
from .security import RequestContext
from .space import App, build_app

NO_PRESYNC = {"serve", "bootstrap", "doctor", "init-user", "sync", "resolve", "token", "restore", "backups", "call", "ops",
              "mcp", "agent-instructions"}


def _print(obj) -> None:
    if isinstance(obj, str):
        print(obj, end="" if obj.endswith("\n") else "\n")
    else:
        print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def _print_steps(steps: list[dict], user: str | None) -> None:
    from .commands import Command, lines
    if not steps:
        print("Nothing to do for this job right now.")
    for i, step in enumerate(steps, start=1):
        print(f"{i}. {step['text']}")
        if step["file"]:
            print(f"   File: {step['file']}")
        for c in step["commands"]:
            for line in lines(Command.from_dict(c), user):
                print(f"   {line['label'] + ': ' if line['label'] else ''}{line['text']}" + (f"   # {c['note']}" if c["note"] else ""))
        print()


def _ctx(app: App, user: str) -> RequestContext:
    token = os.environ.get("RB_ACTOR_TOKEN")
    if token:
        from .services.space_ops import actor_for_token
        actor = actor_for_token(app.space(user), token)
        if actor is None:
            raise AppError("RB_ACTOR_TOKEN isn't a valid token for this user.")
        return RequestContext(actor=actor, roles=(actor,), user_id=user, channel="cli", tenant_id=app.tenant_id)
    return RequestContext.owner(user)


def _user(app: App, args) -> str:
    user = args.user or app.settings.default_user
    if not user:
        users, _ = app.discover_users()
        if len(users) == 1:
            return users[0]
        raise AppError("Which user? Pass --user <name> or set RB_DEFAULT_USER.")
    return user


def _pick(prompt: str, options: list[dict]) -> str | None:
    print(prompt)
    for i, o in enumerate(options, start=1):
        print(f"  {i}) {o['name']}  [{o['slug']}]")
    print(f"  {len(options) + 1}) add a new one")
    while True:
        choice = input("Choose a number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options) + 1:
            return None if int(choice) == len(options) + 1 else options[int(choice) - 1]["slug"]


def _new_job_interactive(app: App, ctx: RequestContext, args) -> dict:
    company = args.company or input("Company name (as shown in the job description): ").strip()
    matches = invoke(app, ctx, "org_suggest", {"name": company})
    create = args.create
    if not any(m["slug"] == company for m in matches) and sys.stdin.isatty():
        if matches:
            picked = _pick(f"Companies matching '{company}':", matches)
            if picked:
                company = picked
            else:
                create = True
        else:
            create = input(f"'{company}' is new. Add it to organizations.yaml? [Y/n] ").strip().lower() in ("", "y", "yes")
    department = args.department
    if department is None and sys.stdin.isatty() and not args.company:
        department = input("Department (optional, Enter to skip): ").strip() or None
    if department and sys.stdin.isatty():
        reg = invoke(app, ctx, "org_list", {})
        slug = next((c["slug"] for c in reg["companies"] if c["slug"] == company), None)
        if slug:
            dm = invoke(app, ctx, "org_suggest", {"name": department, "company": slug})
            if dm and not any(m["slug"] == department for m in dm):
                picked = _pick(f"Departments matching '{department}':", dm)
                department = picked or department
                create = create or picked is None
    return invoke(app, ctx, "job_new", {"company": company, "department": department, "title": args.title,
                                        "posting_url": args.url, "source": args.source, "create_missing": create})


INTAKE_FIELDS = ("title", "company", "department", "location", "work_arrangement", "salary_range", "date_posted",
                 "closing_date", "external_id", "posting_url", "source")
ORIGIN_LABEL = {"structured": "page data", "rule": "posting text", "adapter": "site adapter", "agent": "agent", "you": "you"}


def _overrides(args) -> dict:
    """The user's own field values: --set FIELD=VALUE, plus --title and --source."""
    overrides = {}
    for item in args.set:
        name, sep, value = item.partition("=")
        if not sep or name.strip() not in INTAKE_FIELDS:
            raise AppError(f"--set needs FIELD=VALUE with FIELD one of: {', '.join(INTAKE_FIELDS)}.")
        overrides[name.strip()] = value.strip() or None
    for name in ("title", "source"):  # new-job's own options are your values too
        if getattr(args, name, None):
            overrides.setdefault(name, getattr(args, name))
    return overrides


def _confirm_job(app: App, ctx: RequestContext, args) -> int:
    """`rb confirm-job <n>`: the same review as `rb new-job <url>`, for a job the agent proposed."""
    overrides = _overrides(args)
    view = invoke(app, ctx, "job_intake_proposals", {"proposal_id": args.proposal_id})[0]
    if view["status"] != "pending":
        raise AppError(f"Proposed job #{args.proposal_id} was already {view['status']}.")
    print(f"Proposed by {view['proposed_by']} on {view['proposed_at'][:10]}" + (f": {view['note']}" if view["note"] else ""))
    keep = ("company", "department", "create_missing", "overrides", "recapture", "replace_posting", "allow_duplicate")
    return _review_and_create(
        app, ctx, args, view["preview"], overrides,
        submit=lambda payload: invoke(app, ctx, "job_intake_confirm",
                                      {"proposal_id": args.proposal_id, **{k: payload[k] for k in keep}}),
        interactive=not args.yes and sys.stdin.isatty())


def _intake(app: App, ctx: RequestContext, args) -> int:
    """`rb new-job <url>` or `rb new-job --stdin`: preview, then (after one review) create the job
    (PRD R13.19–R13.21)."""
    text = sys.stdin.read().lstrip("﻿") if args.stdin else None
    if args.stdin and not (text or "").strip():
        raise AppError("Nothing came in on stdin. Pipe the posting text: rb new-job --stdin [url] < posting.txt")
    overrides = _overrides(args)

    if args.file:  # read by the file adapter, from the intake folder only
        preview = invoke(app, ctx, "job_intake_preview_file", {"file": args.file, "url": args.url})
    else:
        preview = invoke(app, ctx, "job_intake_preview", {"url": args.url, "text": text})
    return _review_and_create(app, ctx, args, preview, overrides,
                              submit=lambda payload: invoke(app, ctx, "job_intake", payload),
                              interactive=not args.yes and not args.stdin and sys.stdin.isatty())


def _review_and_create(app: App, ctx: RequestContext, args, preview: dict, overrides: dict, submit, interactive: bool) -> int:
    """Show the preview, settle a duplicate and the company, let the user change fields, then submit
    (job_intake for `rb new-job`, job_intake_confirm for `rb confirm-job`)."""
    _show_preview(preview, overrides)
    payload = {"capture_id": preview["capture_id"], "fields": preview["fields"], "overrides": overrides,
               "company": args.company, "department": args.department, "create_missing": args.create,
               "recapture": args.recapture, "replace_posting": args.replace, "allow_duplicate": args.allow_duplicate}

    dups = preview["duplicates"]
    if dups and not args.recapture and not args.allow_duplicate:
        first = dups[0]["job_id"]
        if not interactive:
            print(f"\nYou're already tracking this posting as {first}. Run again with --recapture {first} to save this "
                  "posting for that job, or --allow-duplicate to start a new one.")
            return 1
        if _ask(f"Save this posting for {first} instead of starting a new job? [Y/n] ", default=True):
            payload["recapture"] = first
        elif not _ask("Start a separate new job anyway? [y/N] ", default=False):
            print("Nothing was created.")
            return 1
        else:
            payload["allow_duplicate"] = True

    if not payload["recapture"]:
        company = preview["company"]
        if not args.company and not company["suggested"]:
            if not interactive:
                found = f" The posting names `{company['name']}`." if company["name"] else ""
                print(f"\nWhich company is this job at?{found} Run again with --company <slug or name> "
                      "(add --create to add a new one to your registry).")
                return 1
            choice = _pick("Which company is this job at?", company["candidates"]) if company["candidates"] else None
            if choice:
                payload["company"] = choice
            else:
                name = company["name"] or input("Company name (as the posting shows it): ").strip()
                if not name or not _ask(f"Add '{name}' to your registry? [Y/n] ", default=True):
                    print("Nothing was created.")
                    return 1
                payload["company"], payload["create_missing"] = name, True
        dept = preview["department"]
        if interactive and not args.department and dept["name"] and not dept["suggested"]:
            if _ask(f"The posting names the department '{dept['name']}'. Add it to your registry? [y/N] ", default=False):
                payload["department"], payload["create_missing"] = dept["name"], True
        if interactive:
            while True:
                ans = input("\nCreate this job? [Y/n], or type a field name to change it: ").strip().lower()
                if ans in ("", "y", "yes"):
                    break
                if ans in ("n", "no", "q"):
                    print("Nothing was created. The captured posting is kept; run the same command again to use it.")
                    return 1
                if ans in INTAKE_FIELDS and ans not in ("company", "department"):
                    overrides[ans] = input(f"{ans} (empty to clear): ").strip() or None
                    _show_preview(preview, overrides, fields_only=True)
                else:
                    print(f"Type one of: {', '.join(f for f in INTAKE_FIELDS if f not in ('company', 'department'))}"
                          " (use --company/--department for those).")
        elif not args.yes:
            print("\nReview the preview, then run the same command with --yes to create the job "
                  "(add --set FIELD=VALUE to change a value).")
            return 0

    result = submit(payload)
    used_file = (f"The posting is saved with the job, so you can delete {preview['source_path']} now."
                 if preview.get("source_path") else None)
    if result.get("recaptured"):
        print(f"\nSaved the posting for {result['job_id']}.")
        if result.get("note"):
            print(result["note"])
        if used_file:
            print(used_file)
        return 0
    print(f"\nCreated {result['job_id']} in {result['folder']}")
    for kind, slug in result["created"].items():
        print(f"  Added {kind} `{slug}` to organizations.yaml.")
    if result.get("note"):
        print(f"  Note: {result['note']}")
    if used_file:
        print(f"  {used_file}")
    print("Next:")
    for step in result["next"]:
        print(f"  - {step}")
    return 0


def _ask(prompt: str, default: bool) -> bool:
    ans = input(prompt).strip().lower()
    return default if not ans else ans in ("y", "yes")


def _show_preview(preview: dict, overrides: dict, fields_only: bool = False) -> None:
    rows = {f["name"]: (f["value"], ORIGIN_LABEL.get(f["origin"], f["origin"])) for f in preview["fields"]}
    for name, value in overrides.items():
        rows[name] = (value, "you") if value else ("", "you: cleared")
    if not fields_only:
        print(f"Posting: {preview['source']}" + (f"  (read by site adapter `{preview['adapter']}`)" if preview["adapter"] else ""))
        if preview["job_id"]:
            print(f"Job id:  {preview['job_id']}")
    print()
    for name in INTAKE_FIELDS:
        if name in ("company", "department"):
            continue
        if name in rows and rows[name][0]:
            value, origin = rows[name]
            print(f"  {name:17} {value}  [{origin}]")
    missing = [n for n in INTAKE_FIELDS if n not in ("company", "department") and not (n in rows and rows[n][0])]
    if missing:
        print(f"  Not stated in the posting: {', '.join(missing)}")
    if fields_only:
        return
    for label, key in (("company", "company"), ("department", "department")):
        m = preview[key]
        if m["suggested"]:
            cand = next(c for c in m["candidates"] if c["slug"] == m["suggested"])
            print(f"  {label:17} {cand['name']} -> `{m['suggested']}` (in your registry)")
        elif m["name"]:
            near = ", ".join(f"`{c['slug']}`" for c in m["candidates"])
            print(f"  {label:17} {m['name']} (not in your registry" + (f"; similar: {near})" if near else ")"))
    for line in preview["disagreements"] + preview["notes"]:
        print(f"  Note: {line}")
    for d in preview["duplicates"]:
        print(f"  Already tracking: {d['job_id']} ({', '.join(d['reasons'])}" + (", archived)" if d["archived"] else ")"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rb", description="resume-builder: honest tailored resumes and an application tracker.")
    p.add_argument("--user", help="user folder name (default: RB_DEFAULT_USER)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def cmd(name, help_):
        return sub.add_parser(name, help=help_, description=help_)

    cmd("serve", "Run bootstrap, then the website, API and sync watcher.")
    b = cmd("bootstrap", "Check mounts, set up and repair user folders, sync.")
    b.add_argument("--check", action="store_true", help="change nothing (same as doctor)")
    cmd("doctor", "Check everything without changing anything.")
    i = cmd("init-user", "Create a user folder from the seed.")
    i.add_argument("name")
    i.add_argument("--display-name")
    i.add_argument("--timezone", default="UTC")

    s = cmd("sync", "Validate changed files and apply them to the database.")
    s.add_argument("--check", action="store_true", help="validate only")
    r = cmd("resolve", "Resolve a sync conflict.")
    r.add_argument("file")
    r.add_argument("--keep", choices=["file", "app"], required=True)
    cmd("status", "Sync status, problems, example entries left, limits.")
    cmd("export-files", "Recreate missing working-copy files from the database.")

    n = cmd("new-job", "Start tracking a job. With the posting's link (or --stdin and its text), the job is filled in "
                       "from the posting and you review it once; without, it asks for the company and department.")
    n.add_argument("link", nargs="?", metavar="url",
                   help="the posting's link: fetched once and read (same as --url); with --stdin, only recorded as its link")
    n.add_argument("--url", help="the posting's link (same as the positional url)")
    n.add_argument("--file", metavar="intake/NAME.txt",
                   help="read the posting text from a file in your intake folder instead of fetching the link")
    n.add_argument("--stdin", action="store_true", help="read the posting text from stdin instead of fetching the link")
    n.add_argument("--company", help="company slug or name (with a posting: default is the match suggested from it)")
    n.add_argument("--department", help="department slug or name")
    n.add_argument("--title", help="job title (with a posting: replaces the one read from it)")
    n.add_argument("--source", choices=["linkedin", "referral", "company_site", "recruiter", "job_board", "other"],
                   help="where you found it (with a posting: replaces the one read from the link)")
    n.add_argument("--create", action="store_true", help="add the company/department to the registry if none matches")
    n.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE",
                   help="with a posting: your own value for a field, e.g. --set work_arrangement=hybrid (empty clears it)")
    n.add_argument("--recapture", metavar="JOB_ID", help="with a posting: save it for an existing job instead of creating one")
    n.add_argument("--replace", action="store_true", help="with --recapture: replace posting text you pasted or edited")
    n.add_argument("--allow-duplicate", action="store_true", help="with a posting: create a new job even if one looks like the same posting")
    n.add_argument("--yes", "-y", action="store_true", help="with a posting: accept the preview without asking")
    ls = cmd("list", "List applications.")
    ls.add_argument("--stage")
    ls.add_argument("--company")
    ls.add_argument("--all", action="store_true", help="include archived")
    sh = cmd("show", "Show one application.")
    sh.add_argument("job_id")
    nx = cmd("next", "What to do next for a job, with the commands to run.")
    nx.add_argument("job_id")
    e = cmd("event", "Add an event to an application.")
    e.add_argument("job_id")
    e.add_argument("--type", default="stage_change", choices=["stage_change", "interview", "follow_up_sent", "message_received", "note", "document_sent", "correction"])
    e.add_argument("--stage")
    e.add_argument("--date", default=None, help="YYYY-MM-DD (default today)")
    e.add_argument("--notes")
    e.add_argument("--contact")
    e.add_argument("--documents", nargs="*", default=[], help="snapshot ids you sent")
    e.add_argument("--corrects", type=int)
    cmd("job-drafts", "Jobs proposed by the agent, waiting for you.")
    cj = cmd("confirm-job", "Review a job the agent proposed and create it (the same review as new-job <url>).")
    cj.add_argument("proposal_id", type=int)
    cj.add_argument("--company", help="company slug or name (default: the match suggested from the posting)")
    cj.add_argument("--department", help="department slug or name")
    cj.add_argument("--title", help="job title, replacing the proposed one")
    cj.add_argument("--source", choices=["linkedin", "referral", "company_site", "recruiter", "job_board", "other"])
    cj.add_argument("--create", action="store_true", help="add the company/department to the registry if none matches")
    cj.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE", help="your own value for a field")
    cj.add_argument("--recapture", metavar="JOB_ID", help="save the posting for an existing job instead of creating one")
    cj.add_argument("--replace", action="store_true", help="with --recapture: replace posting text you pasted or edited")
    cj.add_argument("--allow-duplicate", action="store_true", help="create a new job even if one looks like the same posting")
    cj.add_argument("--yes", "-y", action="store_true", help="accept the proposal without asking")
    dj = cmd("discard-job", "Discard a job the agent proposed (nothing is created).")
    dj.add_argument("proposal_id", type=int)
    cmd("event-drafts", "Events proposed by the agent, waiting for you.")
    ce = cmd("confirm-event", "Confirm a proposed event.")
    ce.add_argument("draft_id", type=int)
    de = cmd("discard-event", "Discard a proposed event.")
    de.add_argument("draft_id", type=int)
    a = cmd("archive-job", "Archive an application (nothing is deleted).")
    a.add_argument("job_id")
    a.add_argument("--restore", action="store_true")

    cmd("orgs", "List companies and departments.")
    ac = cmd("add-company", "Add a company.")
    ac.add_argument("name")
    ac.add_argument("--slug")
    ac.add_argument("--force", action="store_true")
    ad = cmd("add-department", "Add a department under a company.")
    ad.add_argument("company")
    ad.add_argument("name")
    ad.add_argument("--force", action="store_true")
    rn = cmd("rename-org", "Rename a company or department (old name kept as alias).")
    rn.add_argument("company")
    rn.add_argument("new_name")
    rn.add_argument("--department")
    mg = cmd("merge-orgs", "Merge a duplicate company into another.")
    mg.add_argument("keep")
    mg.add_argument("merge")

    br = cmd("brief", "Print the agent brief for a job (give it to your AI tool).")
    br.add_argument("job_id")
    br.add_argument("--for", dest="mode", choices=["chat", "mcp", "api"], default="chat",
                    help="how the agent hands work back: chat (copy-paste, default), mcp tools, or the HTTP API")
    ai = cmd("agent-instructions", "Print standing instructions to paste into an AI tool's custom instructions.")
    ai.add_argument("--for", dest="mode", choices=["chat", "mcp", "api"], default="chat")
    pr = cmd("propose", "Submit a draft from a chat assistant (read from stdin), checked as the agent's work.")
    pr.add_argument("job_id", help="job id, or `public` for a profile")
    pr.add_argument("kind", choices=["profile", "fit_report", "cover_letter", "answer"])
    pr.add_argument("--slug", help="for answers (default why-here)")
    pr.add_argument("--question", help="for answers other than why-here")
    cmd("mcp", "Run the MCP server on stdin/stdout for MCP-capable AI tools (acts as the agent).")
    cf = cmd("confirm", "Confirm a draft bullet or other library item, or all drafts with --all.")
    cf.add_argument("id", nargs="?")
    cf.add_argument("--all", action="store_true", help="confirm every draft (lists them and asks first)")
    cf.add_argument("--yes", "-y", action="store_true", help="with --all: skip the question")
    ap = cmd("approve", "Approve a cover letter or answer.")
    ap.add_argument("job_id")
    ap.add_argument("kind", choices=["cover_letter", "answer"])
    ap.add_argument("--slug")

    rd = cmd("render", "Snapshot a document and export it to output/.")
    rd.add_argument("job_key", help="job id, or `public`")
    rd.add_argument("--type", default="resume", choices=["resume", "cover_letter", "answer", "fit_report"])
    rd.add_argument("--slug")
    rd.add_argument("--preview", action="store_true", help="include drafts, watermarked")
    rd.add_argument("--ats", action="store_true", help="plain single-column variant")
    rd.add_argument("--format", default="pdf", choices=["pdf", "html", "txt", "none"])
    rg = cmd("regenerate", "Rebuild a document exactly from a snapshot.")
    rg.add_argument("snapshot_id", nargs="?")
    rg.add_argument("--job")
    rg.add_argument("--type")
    rg.add_argument("--as-of")
    rg.add_argument("--format", default="pdf", choices=["pdf", "html", "txt"])
    sn = cmd("snapshots", "List saved snapshots.")
    sn.add_argument("job_key", nargs="?")
    sn.add_argument("--type")
    for name, help_ in (("pin", "Pin a snapshot."), ("unpin", "Unpin a snapshot.")):
        x = cmd(name, help_)
        x.add_argument("snapshot_id")
    ds = cmd("delete-snapshot", "Delete a snapshot.")
    ds.add_argument("snapshot_id")
    ds.add_argument("--confirm", action="store_true")
    df = cmd("diff", "Compare two snapshots.")
    df.add_argument("a")
    df.add_argument("b")
    cmd("publish", "Render the public profile as a static site in output/<user>/public/.")
    cmd("clean-output", "Delete exported files (they can be regenerated).")

    cmd("dashboard", "Search statistics.")
    cmd("actions", "Upcoming actions and stale applications.")
    cmd("web-login", "Print a one-time link that signs you in to the tracker website (valid 10 minutes).")
    t = cmd("token", "Issue an API token (replaces the previous one).")
    t.add_argument("--actor", choices=["owner", "agent"], default="agent")
    cmd("backup", "Back up the database now.")
    cmd("backups", "List backups.")
    rs = cmd("restore", "Restore the database from a backup.")
    rs.add_argument("file")
    cmd("audit", "Recent audit log entries.")
    cmd("ops", "List every operation and its permission.")
    c = cmd("call", "Call any operation with JSON input.")
    c.add_argument("operation")
    c.add_argument("json", nargs="?", default="{}")
    return p


def drop_root() -> None:
    """Re-run `rb` as the host user when started as root inside the container.

    `docker compose exec app sh` skips the entrypoint, so the shell is root. Files `rb` wrote
    from there (database, backups, write-back, job folders) would be root-owned and the server,
    running as PUID:PGID, could no longer change them. Switch user the same way the entrypoint does.
    """
    geteuid = getattr(os, "geteuid", None)
    puid, pgid = os.environ.get("PUID", ""), os.environ.get("PGID", "")
    if geteuid is None or geteuid() != 0 or not puid.isdigit() or not pgid.isdigit() or puid == "0":
        return
    os.execvp("setpriv", ["setpriv", f"--reuid={puid}", f"--regid={pgid}", "--clear-groups", sys.executable, "-m", "resume_builder.cli", *sys.argv[1:]])


def main(argv: list[str] | None = None) -> int:
    if argv is None:  # a real command line, not a test calling main([...])
        drop_root()
    args = build_parser().parse_args(argv)
    app = build_app()
    try:
        return _run(app, args) or 0
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        for prob in exc.problems:
            print(f"  - {prob.render()}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        app.close()


def _run(app: App, args) -> int | None:
    from .sync_ops import bootstrap
    if args.cmd == "serve":
        from .server import serve
        return serve(app)
    if args.cmd in ("bootstrap", "doctor"):
        report = bootstrap(app, check_only=args.cmd == "doctor" or getattr(args, "check", False))
        print_bootstrap(report)
        return 0
    if args.cmd == "ops":
        for op in sorted(app.registry.values(), key=lambda o: o.name):
            print(f"{op.name:22} {op.permission:22} {'write' if op.writes else 'read '}  {op.summary}")
        return 0
    if args.cmd == "init-user":
        ctx = RequestContext.owner(args.name)
        _print(invoke(app, ctx, "space_init", {"display_name": args.display_name, "timezone": args.timezone}))
        return 0

    if args.cmd == "agent-instructions":
        from .services.profiles import agent_instructions
        _print(agent_instructions(args.mode))
        return 0
    user = _user(app, args)
    if args.cmd == "mcp":
        from .mcp import serve_stdio
        space = app.space(user)
        sync_mod.run_sync(space, RequestContext.system(user))
        return serve_stdio(app, user)
    ctx = _ctx(app, user)
    if args.cmd not in NO_PRESYNC:
        report = sync_mod.run_sync(app.space(user), RequestContext.system(user))  # quick: unchanged files are skipped
        if not report.ok:
            print("Note: some files have problems; see SYNC-STATUS.md (`rb status`).", file=sys.stderr)

    def op(name, payload=None):
        return invoke(app, ctx, name, payload or {})

    c = args.cmd
    if c == "sync":
        result = op("sync", {"check_only": args.check})
        print_sync(result)
        return 0 if result["ok"] else 2
    if c == "resolve":
        print_sync(op("resolve", {"file": args.file, "keep": args.keep}))
    elif c == "status":
        _print(op("space_status"))
    elif c == "export-files":
        _print(op("export_files"))
    elif c == "new-job":
        if args.link and args.url and args.link != args.url:
            raise AppError("Give the posting's link once: either `rb new-job <url>` or `--url <url>`.")
        args.url = args.link or args.url
        if args.file and args.stdin:
            raise AppError("Use --file or --stdin, not both.")
        if args.url or args.stdin or args.file:  # a posting to read: job intake (PRD 8.13)
            return _intake(app, ctx, args)
        _print(_new_job_interactive(app, ctx, args))
    elif c == "list":
        rows = op("job_list", {"stage": args.stage, "company": args.company, "include_archived": args.all})
        for r in rows:
            flag = " STALE" if r["stale"] else ""
            print(f"{r['job_id']:52} {r['stage']:13} {(r['title'] or '')[:40]}{flag}")
        if not rows:
            print("No applications yet. Start one with: rb new-job")
    elif c == "show":
        _print(op("job_get", {"job_id": args.job_id}))
    elif c == "next":
        _print_steps(op("job_next_steps", {"job_id": args.job_id}), args.user)
    elif c == "event":
        ev = {"date": args.date or date.today().isoformat(), "type": args.type, "stage": args.stage,
              "notes": args.notes, "contact": args.contact, "documents": args.documents, "corrects": args.corrects}
        _print(op("event_append", {"job_id": args.job_id, "event": ev}))
    elif c == "web-login":
        out = op("web_login_link")
        port = os.environ.get("RB_HOST_PORT") or str(app.settings.port)
        print(f"Open this link to sign in to the tracker (works once, until {out['expires_at']}):")
        print(f"  http://localhost:{port}{out['path']}")
    elif c == "job-drafts":
        rows = op("job_intake_proposals")
        for r in rows:
            p = r["preview"]
            title = next((f["value"] for f in p["fields"] if f["name"] == "title"), "(no title)")
            company = (p["company"]["suggested"] or p["company"]["name"] or "company not named")
            print(f"#{r['proposal_id']:<4} {title[:40]:40} {company[:24]:24} by {r['proposed_by']} {r['proposed_at'][:10]}")
        if not rows:
            print("No jobs waiting. The agent proposes jobs with job_intake_propose.")
        else:
            print("Review one with: rb confirm-job <n>   (or rb discard-job <n>)")
    elif c == "confirm-job":
        return _confirm_job(app, ctx, args)
    elif c == "discard-job":
        _print(op("job_intake_discard", {"proposal_id": args.proposal_id}))
    elif c == "event-drafts":
        _print(op("event_drafts"))
    elif c == "confirm-event":
        _print(op("event_confirm", {"draft_id": args.draft_id}))
    elif c == "discard-event":
        _print(op("event_discard", {"draft_id": args.draft_id}))
    elif c == "archive-job":
        _print(op("job_archive", {"job_id": args.job_id, "archived": not args.restore}))
    elif c == "orgs":
        for comp in op("org_list")["companies"]:
            print(f"{comp['slug']:30} {comp['name']}")
            for d in comp["departments"]:
                print(f"  {d['slug']:28} {d['name']}")
    elif c == "add-company":
        _print(op("company_add", {"name": args.name, "slug": args.slug, "force": args.force}))
    elif c == "add-department":
        _print(op("department_add", {"company": args.company, "name": args.name, "force": args.force}))
    elif c == "rename-org":
        _print(op("org_rename", {"company": args.company, "department": args.department, "new_name": args.new_name}))
    elif c == "merge-orgs":
        _print(op("org_merge", {"keep": args.keep, "merge": args.merge}))
    elif c == "brief":
        _print(op("agent_brief", {"job_id": args.job_id, "mode": args.mode}))
    elif c == "propose":
        # Windows PowerShell 5.1 starts piped text with a byte-order mark; it isn't part of the draft.
        text = sys.stdin.read().lstrip("﻿")
        if not text.strip():
            raise AppError("Nothing on stdin. Pipe the draft in, e.g. `pbpaste | rb propose <job-id> profile`.")
        # Pasted drafts are submitted as the agent's work, so they get the agent's strict checks (B7).
        agent_ctx = RequestContext.agent(user, channel="cli")
        if args.kind == "profile":
            result = invoke(app, agent_ctx, "profile_propose_text", {"key": args.job_id, "text": text})
        else:
            from .yamlio import strip_code_fence
            result = invoke(app, agent_ctx, "document_propose", {
                "job_id": args.job_id, "kind": args.kind, "text": strip_code_fence(text),
                "slug": args.slug, "question": args.question})
        _print(result)
    elif c == "confirm":
        if args.all == bool(args.id):
            raise AppError("Give one id (`rb confirm <id>`) or `--all`, not both.")
        if args.id:
            _print(op("item_confirm", {"id": args.id}))
        else:
            return confirm_all(op, args.yes)
    elif c == "approve":
        _print(op("document_approve", {"job_id": args.job_id, "kind": args.kind, "slug": args.slug}))
    elif c == "render":
        _print(op("render", {"job_key": args.job_key, "doc_type": args.type, "slug": args.slug, "preview": args.preview,
                             "variant": "ats" if args.ats else "styled", "export": None if args.format == "none" else args.format}))
    elif c == "regenerate":
        _print(op("regenerate", {"snapshot_id": args.snapshot_id, "job_key": args.job, "doc_type": args.type,
                                 "as_of": args.as_of, "format": args.format}))
    elif c == "snapshots":
        for s in op("snapshot_list", {"job_key": args.job_key, "doc_type": args.type}):
            lock = " protected" if s["protected"] else ""
            print(f"{s['id']}  {s['job_key']:40} {s['doc_type']:12} {s['reason']:9} {s['created_at']}{lock}")
    elif c == "pin":
        _print(op("snapshot_pin", {"snapshot_id": args.snapshot_id}))
    elif c == "unpin":
        _print(op("snapshot_unpin", {"snapshot_id": args.snapshot_id}))
    elif c == "delete-snapshot":
        _print(op("snapshot_delete", {"snapshot_id": args.snapshot_id, "confirm_protected": args.confirm}))
    elif c == "diff":
        _print(op("snapshot_diff", {"a": args.a, "b": args.b}) or "(no differences)")
    elif c == "publish":
        _print(op("publish_public"))
    elif c == "clean-output":
        _print(op("clean_output"))
    elif c == "dashboard":
        _print(op("dashboard"))
    elif c == "actions":
        _print(op("actions"))
    elif c == "token":
        _print(op("token_issue", {"actor": args.actor}))
    elif c == "backup":
        _print(op("backup"))
    elif c == "backups":
        _print(op("backups_list"))
    elif c == "restore":
        _print(op("restore", {"file": args.file}))
    elif c == "audit":
        for e in op("audit_list"):
            print(f"{e['at']}  {e['actor']:7} {e['channel']:10} {e['operation']:24} {e['target'][:40]:40} {e['result']}")
    elif c == "call":
        _print(op(args.operation, json.loads(args.json)))
    return 0


def print_sync(result: dict) -> None:
    files = result["files"]
    if not files:
        print("Nothing changed since the last sync.")
    for f in files:
        mark = {"applied": "✓", "failed": "✗", "conflict": "⚠", "waiting": "…", "missing": "?"}.get(f["result"], "·")
        print(f"{mark} {f['file']} ({f['result']})")
        for p in f["problems"]:
            loc = ", ".join(x for x in [f"line {p['line']}" if p.get("line") else "", f"`{p['field']}`" if p.get("field") else ""] if x)
            print(f"    {'warning' if p['severity'] == 'warning' else 'problem'}{' (' + loc + ')' if loc else ''}: {p['message']}"
                  + (f" Example: {p['example']}" if p.get("example") else ""))
    if result.get("backup"):
        print(f"(Database backed up first: {result['backup']})")


def confirm_all(op, yes: bool, ask=input) -> int:
    """`rb confirm --all`: list every draft, ask, then confirm them in one change."""
    preview = op("library_confirm_all", {"dry_run": True})
    if preview["skipped_examples"]:
        print(f"Skipping {len(preview['skipped_examples'])} example entries (they can't be confirmed): replace them with your own.")
    if not preview["count"]:
        print("Nothing to confirm: no drafts in your content library.")
        return 0
    print(f"{preview['count']} draft item(s) would be confirmed:\n")
    for it in preview["items"]:
        text = it["text"] if len(it["text"]) <= 90 else it["text"][:87] + "..."
        print(f"  {it['id']}  ({it['kind']})\n      {text}")
    print("\nConfirmed items can appear on real resumes, letters and answers.")
    print("Only confirm what you've checked is accurate: wording, tools, dates and metrics.")
    if not yes:
        if not sys.stdin.isatty():
            raise AppError("Not confirming without an answer. Run it in a terminal, or add --yes.")
        try:
            answer = ask(f"Confirm all {preview['count']}? Type 'yes' to continue: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("yes", "y"):
            print("Nothing changed.")
            return 1
    done = op("library_confirm_all", {"dry_run": False, "ids": [it["id"] for it in preview["items"]]})
    print(f"Confirmed {done['count']} item(s). base.yaml has been updated.")
    return 0


def print_bootstrap(report: dict) -> None:
    for p in report.get("folders", []):
        print(f"! {p}")
    if report.get("created_user"):
        print(f"Created your user folder: {report['created_user']} (see START-HERE.md inside it)")
    if report.get("hint"):
        print(report["hint"])
    for user, entry in report.get("users", {}).items():
        st = entry["status"]
        print(f"\n== {user} ==")
        for c in entry.get("created") or []:
            print(f"  created {c}")
        print_sync(entry["sync"])
        if st["example_entries_left"]:
            print(f"  Example entries still in base.yaml: {', '.join(st['example_entries_left'])}")
        if st["missing_contact_secrets"]:
            print(f"  Contact details not set: {', '.join(st['missing_contact_secrets'])} (add them to .env)")
        if st["snapshots"]["near_limit"]:
            print(f"  Saved documents near the limit: {st['snapshots']['count']} of {st['snapshots']['limit']}")
        agent = st.get("agent") or {}
        if agent.get("state") == "connected":
            print(f"  AI agent: connected ({agent['direct_channel']}), last active {agent['direct_active']}")
        elif agent.get("state") == "copy_and_paste":
            print(f"  AI agent: used through copy and paste (rb propose), last on {agent['last_active']}; "
                  "no assistant has connected directly (see docs/AI-AGENTS.md)")
        elif agent.get("state") == "token_unused":
            print("  AI agent: token issued, not used yet (see docs/AI-AGENTS.md)")
        else:
            print("  AI agent: not set up yet (see docs/AI-AGENTS.md)")
        if agent.get("stale_token"):
            print("  Your assistant used a replaced API token: issue a new one with rb token --actor agent")
        if st.get("job_proposals_waiting"):
            print(f"  {st['job_proposals_waiting']} proposed job(s) waiting: rb job-drafts")
        if st["event_drafts_waiting"]:
            print(f"  {st['event_drafts_waiting']} proposed event(s) waiting: rb event-drafts")


if __name__ == "__main__":
    sys.exit(main())
