"""HTTP API (generated from the operation registry) and the tracker website.

The website is read-only unless the owner signs in (issue #30, PRD 8.8, D32): then proposed jobs and
events get Confirm and Discard buttons, which run the same operations as the CLI as the owner on
channel `web`. Sign-in, sessions and forged-request checks are in `web/session.py`.

PRD A5: every operation in the registry becomes `POST /api/{user}/{operation}` with its typed
input, and the OpenAPI spec is generated from the same definitions. The website only calls
operations too; it never reads files or the database directly.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, Environment, FileSystemLoader

from . import __version__
from .errors import AppError, PermissionDenied
from .models import FINAL_STAGES
from .operations import invoke
from .security import RequestContext
from .space import App
from .services.space_ops import actor_for_token

WEB_DIR = Path(__file__).parent / "web"


def context_for(app: App, user: str, authorization: str | None) -> RequestContext:
    """Who is calling (B9a): a valid token names the actor; no token means read-only."""
    space = app.space(user)
    if authorization:
        token = authorization.removeprefix("Bearer ").strip()
        actor = actor_for_token(space, token)
        if actor is None:
            from .services.agent_status import note_replaced_token
            from .services.space_ops import hash_token
            if space.repo.replaced_token_actor(hash_token(token)) == "agent":
                note_replaced_token(space)  # shown on the agent status: "your assistant's token was replaced"
            raise PermissionDenied("That token isn't valid (it may have been replaced). Issue a new one with `rb token`.")
        return RequestContext(actor=actor, roles=(actor,), user_id=user, channel="api", tenant_id=app.tenant_id)
    return RequestContext.anonymous(user, tenant_id=app.tenant_id)


def _check_user(app: App, user: str) -> None:
    users, _ = app.discover_users()
    if user not in users:
        raise HTTPException(404, f"No user `{user}`.")


def create_api(app: App, routers: tuple | list = ()) -> FastAPI:
    """The FastAPI app for `app`. Prefer `resume_builder.web.app.create_app`, which wraps this."""
    api = FastAPI(
        title="resume-builder",
        version=__version__,
        description="Every operation is `POST /api/{user}/{operation}`. Send `Authorization: Bearer <token>` "
                    "(from `rb token`) to write; without a token, only read operations are allowed.",
    )

    @api.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": exc.message, "problems": [p.to_dict() for p in exc.problems]})

    @api.get("/health", include_in_schema=False)
    def health():
        return {"ok": True, "version": __version__}

    api.state.rb_app = app  # routers reach the App through request.app.state.rb_app

    @api.middleware("http")
    async def _host_check(request: Request, call_next):
        """Every request, before any route: the Host must be one this app answers to (issue #31).
        A page that rebinds its own name to 127.0.0.1 can't read the tracker through the user's browser."""
        from .web.session import host_allowed

        if not host_allowed(app, request.headers.get("host")):
            return PlainTextResponse(
                "This address isn't allowed. Open the tracker at http://localhost (with your port), or add the "
                "name to RB_WEB_HOSTS if it points to this computer.", status_code=400)
        return await call_next(request)

    for op in sorted(app.registry.values(), key=lambda o: o.name):
        api.add_api_route(
            f"/api/{{user}}/{op.name}", _endpoint(app, op), methods=["POST"], name=op.name,
            summary=op.summary, description=f"Permission: `{op.permission}`" + (" · writes" if op.writes else ""),
            tags=[op.permission.split(":")[0]],
        )
    for router in [*app.extensions.routers, *routers]:
        api.include_router(router)
    _mcp_http(api, app)
    _website(api, app)
    return api


def web_templates(app: App) -> Jinja2Templates:
    """Website templates: the App's override directories first, then the packaged ones."""
    dirs = [*app.extensions.template_dirs, WEB_DIR / "templates"]
    env = Environment(loader=ChoiceLoader([FileSystemLoader(str(d)) for d in dirs]), autoescape=True)
    return Jinja2Templates(env=env)


def _endpoint(app: App, op):
    def endpoint(user: str, payload=None, authorization: Optional[str] = None):
        _check_user(app, user)
        ctx = context_for(app, user, authorization)
        space = app.space(user)
        with space._lock:
            return invoke(app, ctx, op.name, payload.model_dump() if payload is not None else {})

    endpoint.__name__ = f"op_{op.name}"
    endpoint.__signature__ = inspect.Signature([
        inspect.Parameter("user", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
        inspect.Parameter("payload", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Optional[op.input_model], default=Body(None)),
        inspect.Parameter("authorization", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Optional[str], default=Header(None)),
    ])
    return endpoint



# ---------------------------------------------------------------- MCP over HTTP (issue #35)

MCP_MAX_BYTES = 1_000_000


def _mcp_http(api: FastAPI, app: App) -> None:
    """`POST /mcp/{user}`: MCP's Streamable HTTP transport, for assistants on the user's own computer.

    The same tools and handler as `rb mcp` (stdio), as the `agent` actor on channel `mcp-http`. Every
    request needs the agent's token. The Host check (#31) applies to every request; a foreign
    `Origin` is refused too, as the MCP spec requires against DNS rebinding. Answers are plain JSON:
    these tools never need server-initiated messages, so there's no event stream and no session id.
    Local only, like the rest of the site: cloud connectors can't and mustn't reach it (PRD 10).
    """
    from .mcp import SUPPORTED_VERSIONS, McpServer
    from .services.agent_status import note_replaced_token
    from .services.space_ops import hash_token

    def refuse(status: int, message: str, **headers) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status, headers=headers)

    @api.post("/mcp/{user}", include_in_schema=False)
    async def mcp_post(request: Request, user: str):
        _check_user(app, user)
        origin = request.headers.get("origin")
        if origin and origin.lower() != f"{request.url.scheme}://{request.headers.get('host', '')}".lower():
            return refuse(403, "Requests from another website aren't allowed.")
        auth = request.headers.get("authorization") or ""
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not token:
            return refuse(401, "An agent token is required. Issue one with `rb token --actor agent` and send it as "
                               "`Authorization: Bearer <token>`.", **{"WWW-Authenticate": "Bearer"})
        space = app.space(user)
        actor = await run_in_threadpool(actor_for_token, space, token)
        if actor is None:
            if await run_in_threadpool(space.repo.replaced_token_actor, hash_token(token)) == "agent":
                await run_in_threadpool(note_replaced_token, space)
            return refuse(401, "That token isn't valid (it may have been replaced). Issue a new one with "
                               "`rb token --actor agent`.", **{"WWW-Authenticate": "Bearer"})
        if actor != "agent":
            return refuse(403, "MCP is the AI agent's channel: use an agent token (`rb token --actor agent`), "
                               "not the owner's.")
        version = request.headers.get("mcp-protocol-version")
        if version and version not in SUPPORTED_VERSIONS:
            return refuse(400, f"Unsupported MCP protocol version `{version[:40]}`. "
                               f"Supported: {', '.join(SUPPORTED_VERSIONS)}.")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MCP_MAX_BYTES:
            return refuse(413, "The request is too large.")
        body = await request.body()
        if len(body) > MCP_MAX_BYTES:
            return refuse(413, "The request is too large.")
        try:
            message = json.loads(body)
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                                status_code=400)

        server = McpServer(app, user, channel="mcp-http")
        invalid = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}

        def run():
            if isinstance(message, list):
                replies = [invalid if not isinstance(m, dict) else server.handle(m) for m in message]
                replies = [r for r in replies if r is not None]
                return replies or None
            return server.handle(message) if isinstance(message, dict) else invalid

        reply = await run_in_threadpool(run)
        if reply is None:  # only notifications: accepted, nothing to say
            return Response(status_code=202)
        return JSONResponse(reply)

    @api.api_route("/mcp/{user}", methods=["GET", "DELETE", "PUT", "PATCH"], include_in_schema=False)
    def mcp_other(user: str):
        # No server-initiated stream (GET) and no sessions to end (DELETE): POST only.
        return refuse(405, "Use POST: this MCP endpoint answers requests and has no event stream or sessions.",
                      Allow="POST")

# ---------------------------------------------------------------- website (read-only)

def _website(api: FastAPI, app: App) -> None:
    from .web import session as websession

    templates = web_templates(app)

    def signed_in(request: Request, user: str) -> str | None:
        """The owner's session id, if this request carries a valid one (identity only, B4)."""
        space = app.space(user)
        with space._lock:
            return websession.session_id(app, space, request.cookies.get(websession.COOKIE))

    def page(request: Request, name: str, status_code: int = 200, **kw):
        user = kw.get("user")
        sid = signed_in(request, user) if user else None
        session = {"signed_in": bool(sid), "csrf": websession.csrf_token(app, user, sid) if sid else None}
        return templates.TemplateResponse(request, name, {"version": __version__, **session, **kw}, status_code=status_code)

    def owner_ctx(user: str) -> RequestContext:
        return RequestContext.owner(user, channel="web", tenant_id=app.tenant_id)

    def run_owner(user: str, op: str, payload: dict | None = None):
        space = app.space(user)
        with space._lock:
            return invoke(app, owner_ctx(user), op, payload or {})

    async def owner_form(request: Request, user: str):
        """Every state-changing website request: local Host, same origin, signed in, valid CSRF token."""
        _check_user(app, user)
        websession.check_request_origin(request, app)
        form = await request.form()
        sid = await run_in_threadpool(signed_in, request, user)
        if not sid:
            raise HTTPException(403, "Sign in first: the website is read-only until you do.")
        websession.check_csrf(app, user, sid, form.get("csrf"))
        return sid, form

    @api.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith(("/u/", "/help", "/static/")):
            for k, v in websession.SECURITY_HEADERS.items():
                if k == "Content-Security-Policy" and "/snapshots/" in path:
                    continue  # rendered documents carry their own styles and fonts
                response.headers.setdefault(k, v)
        return response

    def run(user: str, op: str, payload: dict | None = None):
        _check_user(app, user)
        space = app.space(user)
        with space._lock:
            return invoke(app, context_for(app, user, None), op, payload or {})

    @api.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        users, problems = app.discover_users()
        return page(request, "index.html", users=users, folder_problems=problems)

    # ------------------------------------------------------------ online help (issue #34, PRD R8.11)
    # The app's own user guides, not user data: public like the rest of the site, behind the same
    # Host check and security headers. Help links open them in one pop-up window (static/help.js).

    from . import help as helpdocs

    api.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @api.get("/help", response_class=HTMLResponse, include_in_schema=False)
    def help_home(request: Request):
        return page(request, "help_index.html", guides=helpdocs.GUIDES, available=helpdocs.guides_dir() is not None)

    @api.get("/help/search", response_class=HTMLResponse, include_in_schema=False)
    def help_search(request: Request, q: str = ""):
        return page(request, "help_search.html", guides=helpdocs.GUIDES, q=q, hits=helpdocs.search(q[:200]))

    @api.get("/help/{slug}", response_class=HTMLResponse, include_in_schema=False)
    def help_page(request: Request, slug: str):
        rendered = helpdocs.render_guide(slug)
        if rendered is None:
            raise HTTPException(404, "No such help page.")
        return page(request, "help_page.html", guides=helpdocs.GUIDES, doc=rendered)

    @api.get("/u/{user}/", response_class=HTMLResponse, include_in_schema=False)
    def user_home(request: Request, user: str):
        return page(request, "user.html", user=user, status=run(user, "space_status"), actions=run(user, "actions"),
                    agent=run(user, "agent_status"))

    @api.get("/u/{user}/applications", response_class=HTMLResponse, include_in_schema=False)
    def applications(request: Request, user: str, stage: str | None = None, company: str | None = None,
                     department: str | None = None, archived: bool = False, sort: str = "job_id"):
        apps = run(user, "job_list", {"stage": stage or None, "company": company or None,
                                      "department": department or None, "include_archived": archived})
        key = {"job_id": lambda a: a["job_id"], "stage": lambda a: a["stage"], "company": lambda a: a["company"],
               "applied": lambda a: a["applied_on"] or "", "next": lambda a: a["next_action_due"] or "9999",
               "stale": lambda a: not a["stale"]}.get(sort, lambda a: a["job_id"])
        apps.sort(key=key, reverse=sort in ("job_id", "applied"))
        reg = run(user, "org_list")
        from .models import STAGES
        return page(request, "applications.html", user=user, apps=apps, registry=reg, stages=STAGES,
                    f={"stage": stage, "company": company, "department": department, "archived": archived, "sort": sort})

    @api.get("/u/{user}/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
    def job(request: Request, user: str, job_id: str):
        return job_page(request, user, job_id)

    def job_page(request: Request, user: str, job_id: str, error: AppError | None = None, status_code: int = 200):
        detail = run(user, "job_get", {"job_id": job_id})
        docs = run(user, "document_list", {"job_id": job_id})
        snaps = run(user, "snapshot_list", {"job_key": job_id})
        profile = None
        try:
            profile = run(user, "profile_get", {"key": job_id})
        except AppError:
            pass
        public = None
        try:
            public = run(user, "profile_get", {"key": "public"})
        except AppError:
            pass
        captures = list(reversed(run(user, "posting_captures", {"job_id": job_id})))  # newest first
        return page(request, "job.html", status_code=status_code, user=user, job=detail, docs=docs, snaps=snaps,
                    captures=captures, profile_diff=_profile_diff(public, profile), error=error,
                    detail_fields=WEB_DETAIL_FIELDS,
                    steps=next_steps(user, job_id))

    # ------------------------------------------------------------ copy-ready commands (issue #36)

    from . import commands as rbcmd

    def user_flag(user: str) -> str | None:
        """`--user <name>` only when plain `rb` wouldn't pick this user by itself."""
        default = app.settings.default_user
        if default:
            return None if user == default else user
        users, _ = app.discover_users()
        return None if users == [user] else user

    def shown(commands: list, user: str) -> list[dict]:
        flag = user_flag(user)
        out = []
        for c in commands:
            c = c if isinstance(c, rbcmd.Command) else rbcmd.Command.from_dict(c)
            out.append({"lines": rbcmd.lines(c, flag), "note": c.note})
        return out

    from . import prompts as rbprompts

    def prompt_mode(user: str, asked: str | None = None) -> str:
        return asked if asked in rbprompts.MODES else rbprompts.preferred_mode(run(user, "agent_status")["state"])

    def shown_prompt(user: str, name: str, values: dict, mode: str) -> dict:
        """One prompt, ready to show: its text in `mode`, and the rb commands around it."""
        p = rbprompts.BY_NAME[name]
        body = rbprompts.text(p, mode, values)
        return {"name": p.name, "title": p.title, "description": p.description, "text": body,
                "before": shown(rbprompts.commands(p.before, values), user) if body and mode == rbprompts.PASTE else [],
                "after": shown(rbprompts.commands(p.after, values), user) if body and mode == rbprompts.PASTE else [],
                "instead": shown(rbprompts.commands(p.instead, values), user) if body is None else []}

    def next_steps(user: str, job_id: str, mode: str | None = None) -> list[dict]:
        mode = mode or prompt_mode(user)
        out = []
        for s in run(user, "job_next_steps", {"job_id": job_id}):
            ps = [shown_prompt(user, p["name"], p["values"], mode) for p in s.get("prompts", [])]
            out.append({**s, "commands": shown(s["commands"], user), "prompts": [p for p in ps if p["text"]]})
        return out

    def picked_job(user: str, job: str | None) -> tuple[list[dict], str | None]:
        """Open jobs for a picker, and the chosen one (default: the newest). 404 for an unknown job."""
        jobs = [j for j in run(user, "job_list", {}) if j["stage"] not in FINAL_STAGES]
        ids = [j["job_id"] for j in jobs]
        if job and job not in ids:
            run(user, "job_get", {"job_id": job})          # 404 for an unknown job; closed jobs are fine
            ids.append(job)
        return jobs, job or (ids[0] if ids else None)

    @api.get("/u/{user}/prompts", response_class=HTMLResponse, include_in_schema=False)
    def prompts_page(request: Request, user: str, job: str | None = None, mode: str | None = None):
        """Ready-made prompts for the user's AI assistant (issue #37), filled in for the chosen job."""
        jobs, job = picked_job(user, job)
        mode = prompt_mode(user, mode)
        values = {"job_id": job} if job else {}
        groups: dict[str, list] = {}
        for p in rbprompts.PROMPTS:
            groups.setdefault(p.group, []).append(shown_prompt(user, p.name, values, mode))
        return page(request, "prompts.html", user=user, jobs=jobs, job=job, mode=mode, groups=groups,
                    detected=rbprompts.preferred_mode(run(user, "agent_status")["state"]))

    @api.get("/u/{user}/commands", response_class=HTMLResponse, include_in_schema=False)
    def commands_page(request: Request, user: str, job: str | None = None):
        jobs, job = picked_job(user, job)
        resumes = run(user, "snapshot_list", {"job_key": job, "doc_type": "resume"}) if job else []
        resumes = [r for r in resumes if not r["preview"]]
        letter = next((d["approved_snapshot"] for d in (run(user, "document_list", {"job_id": job}) if job else [])
                       if d["kind"] == "cover_letter" and d["status"] == "approved"), None)
        groups = [{"title": g["title"], "commands": shown(g["commands"], user)}
                  for g in rbcmd.catalog(job, resumes[-1]["id"] if resumes else None, letter)]
        return page(request, "commands.html", user=user, jobs=jobs, job=job, groups=groups,
                    steps=next_steps(user, job) if job else [], user_flag=user_flag(user))

    @api.get("/u/{user}/proposals", response_class=HTMLResponse, include_in_schema=False)
    def proposals(request: Request, user: str, notice: str | None = None):
        """Jobs the agent proposed. Signed in, each has Confirm and Discard; otherwise the CLI commands."""
        return proposals_page(request, user, notice={"discarded": "Discarded."}.get(notice or ""))

    def proposals_page(request: Request, user: str, notice: str | None = None, errors: dict | None = None,
                       values: dict | None = None, status_code: int = 200):
        reg = run(user, "org_list")
        return page(request, "proposals.html", status_code=status_code, user=user, proposals=run(user, "job_intake_proposals"),
                    companies=reg["companies"], notice=notice, errors=errors or {}, values=values or {},
                    edit_fields=WEB_INTAKE_FIELDS, answer_fields=WEB_ANSWER_FIELDS)

    # ------------------------------------------------------------ owner sign-in (issue #30)

    @api.get("/u/{user}/login", response_class=HTMLResponse, include_in_schema=False)
    def login_form(request: Request, user: str, code: str | None = None):
        _check_user(app, user)  # showing the form changes nothing; signing in is the POST
        return page(request, "login.html", user=user, code=code, error=None)

    @api.post("/u/{user}/login", include_in_schema=False)
    async def login(request: Request, user: str):
        _check_user(app, user)
        websession.check_request_origin(request, app)
        form = await request.form()
        code, token = (form.get("code") or "").strip(), (form.get("token") or "").strip()

        def sign_in() -> str | None:
            space = app.space(user)
            with space._lock:
                if code:
                    ok, how = websession.use_login_code(space, code), "one-time code"
                else:
                    ok, how = bool(token) and actor_for_token(space, token) == "owner", "owner token"
                if not ok:
                    space.audit(RequestContext.anonymous(user, tenant_id=app.tenant_id), "web:sign-in", result=f"failed: {how}")
                    return None
                cookie = websession.start_session(app, space)
                space.audit(owner_ctx(user), "web:sign-in", result=f"ok: {how}")
                return cookie

        cookie = await run_in_threadpool(sign_in)
        if cookie is None:
            msg = ("That sign-in link has expired or was already used. Run `rb web-login` for a new one." if code
                   else "That isn't a valid owner token. Agent tokens can't sign in. Issue one with `rb token --actor owner`.")
            return await run_in_threadpool(lambda: page(request, "login.html", status_code=403, user=user, code=None, error=msg))
        response = RedirectResponse(f"/u/{user}/", status_code=303)
        response.set_cookie(websession.COOKIE, cookie, httponly=True, samesite="strict", path=f"/u/{user}/",
                            max_age=int(websession.LIFETIME.total_seconds()))
        return response

    @api.post("/u/{user}/logout", include_in_schema=False)
    async def logout(request: Request, user: str):
        sid, _ = await owner_form(request, user)

        def sign_out():
            space = app.space(user)
            with space._lock:
                websession.end_session(space, sid)
                space.audit(owner_ctx(user), "web:sign-out")

        await run_in_threadpool(sign_out)
        response = RedirectResponse(f"/u/{user}/", status_code=303)
        response.delete_cookie(websession.COOKIE, path=f"/u/{user}/")
        return response

    # ------------------------------------------------------------ owner actions (issue #30)

    @api.post("/u/{user}/proposals/{proposal_id}/confirm", include_in_schema=False)
    async def proposal_confirm(request: Request, user: str, proposal_id: int):
        _, form = await owner_form(request, user)

        def act():
            try:
                view = run(user, "job_intake_proposals", {"proposal_id": proposal_id})[0]
                payload = _confirm_payload(form, view["preview"], view.get("from_user") or {})
                out = run_owner(user, "job_intake_confirm", {"proposal_id": proposal_id, **payload})
            except AppError as exc:
                return proposals_page(request, user, errors={proposal_id: exc}, values={proposal_id: dict(form)},
                                      status_code=exc.status_code)
            return RedirectResponse(f"/u/{user}/jobs/{out['job_id']}", status_code=303)

        return await run_in_threadpool(act)

    @api.post("/u/{user}/proposals/{proposal_id}/discard", include_in_schema=False)
    async def proposal_discard(request: Request, user: str, proposal_id: int):
        await owner_form(request, user)

        def act():
            try:
                run_owner(user, "job_intake_discard", {"proposal_id": proposal_id})
            except AppError as exc:
                return proposals_page(request, user, errors={proposal_id: exc}, status_code=exc.status_code)
            return RedirectResponse(f"/u/{user}/proposals?notice=discarded", status_code=303)

        return await run_in_threadpool(act)

    @api.post("/u/{user}/events/{draft_id}/{action}", include_in_schema=False)
    async def event_action(request: Request, user: str, draft_id: int, action: str):
        _, form = await owner_form(request, user)
        if action not in ("confirm", "discard"):
            raise HTTPException(404)
        job_id = str(form.get("job_id") or "")

        def act():
            try:
                run_owner(user, f"event_{action}", {"draft_id": draft_id})
            except AppError as exc:
                return job_page(request, user, job_id, error=exc, status_code=exc.status_code)
            return RedirectResponse(f"/u/{user}/jobs/{job_id}", status_code=303)

        return await run_in_threadpool(act)

    @api.post("/u/{user}/jobs/{job_id}/details", include_in_schema=False)
    async def job_details(request: Request, user: str, job_id: str):
        """The owner's own fields of a job (issue #38): only the ones changed in the form are sent."""
        _, form = await owner_form(request, user)

        def act():
            try:
                current = run(user, "job_get", {"job_id": job_id})
                changes = _details_changes(form, current)
                if changes:
                    run_owner(user, "application_update", {"job_id": job_id, "changes": changes})
            except AppError as exc:
                return job_page(request, user, job_id, error=exc, status_code=exc.status_code)
            return RedirectResponse(f"/u/{user}/jobs/{job_id}", status_code=303)

        return await run_in_threadpool(act)

    @api.post("/u/{user}/updates/{draft_id}/{action}", include_in_schema=False)
    async def update_action(request: Request, user: str, draft_id: int, action: str):
        _, form = await owner_form(request, user)
        if action not in ("confirm", "discard"):
            raise HTTPException(404)
        job_id = str(form.get("job_id") or "")

        def act():
            try:
                run_owner(user, f"application_draft_{action}", {"draft_id": draft_id})
            except AppError as exc:
                return job_page(request, user, job_id, error=exc, status_code=exc.status_code)
            return RedirectResponse(f"/u/{user}/jobs/{job_id}", status_code=303)

        return await run_in_threadpool(act)

    @api.get("/u/{user}/companies/{company}", response_class=HTMLResponse, include_in_schema=False)
    def company(request: Request, user: str, company: str):
        return page(request, "company.html", user=user, view=run(user, "company_view", {"company": company}))

    @api.get("/u/{user}/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard(request: Request, user: str):
        return page(request, "dashboard.html", user=user, d=run(user, "dashboard"))

    @api.get("/u/{user}/diff", response_class=PlainTextResponse, include_in_schema=False)
    def diff(user: str, a: str, b: str):
        return run(user, "snapshot_diff", {"a": a, "b": b}) or "(no differences)"

    @api.get("/u/{user}/snapshots/{sid}.{fmt}", include_in_schema=False)
    def snapshot_file(user: str, sid: str, fmt: str):
        """Rendered on the fly from the snapshot and streamed; nothing is written (R12.15)."""
        from .services.render import load_snapshot, render_html, render_pdf
        _check_user(app, user)
        space = app.space(user)
        with space._lock:
            invoke(app, context_for(app, user, None), "snapshot_get", {"snapshot_id": sid})  # permission check
            content, meta = load_snapshot(space, sid)
            html, warnings = render_html(space, content, meta)
        if fmt == "html":
            return HTMLResponse(html, headers={"X-RB-Warnings": " | ".join(warnings)[:500]})
        if fmt == "pdf":
            pdf = render_pdf(html, meta["created_at"], sid, str(app.settings.templates_dir))
            return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{sid}.pdf"'})
        raise HTTPException(404)

    @api.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


WEB_INTAKE_FIELDS = ("title", "location", "work_arrangement", "salary_range", "date_posted", "closing_date",
                     "external_id", "posting_url", "source")
WEB_ANSWER_FIELDS = ("interest_level", "interest_notes", "next_action", "next_action_due", "notes")  # issue #38
WEB_DETAIL_FIELDS = ("interest_level", "interest_notes", "source", "next_action", "next_action_due", "notes",
                     "fit_summary", "outcome_reason")


def _form_text(value) -> str:
    return "" if value is None else str(value)


def _details_changes(form, current: dict) -> dict:
    """The job-page details form, as application_update changes: only fields that differ; empty clears."""
    changes = {}
    for name in WEB_DETAIL_FIELDS:
        key = f"d_{name}"
        if key in form:
            new = str(form[key]).strip()
            if new != _form_text(current.get(name)).strip():
                changes[name] = new or None
    return changes


def _confirm_payload(form, preview: dict, from_user: dict | None = None) -> dict:
    """The choices in a website Confirm form, as job_intake_confirm input (the same as rb confirm-job)."""
    from .errors import ValidationFailed

    company = (form.get("company") or "").strip() or None
    create = False
    if company == "__new__":
        company = (form.get("new_company") or "").strip()
        if not company:
            raise ValidationFailed("Type the name of the company to add.")
        create = True
    department = (form.get("department") or "").strip() or None
    if form.get("add_department"):
        department = department or (form.get("posting_department") or "").strip() or None
        create = create or bool(department)
    from_user = from_user or {}
    current = {f["name"]: f["value"] for f in preview["fields"]}
    if from_user.get("source"):
        current["source"] = from_user["source"]  # what the agent passed on from you is the starting value
    overrides = {}
    for name in WEB_INTAKE_FIELDS:
        key = f"f_{name}"
        if key in form:
            new = str(form[key]).strip()
            if new != (current.get(name) or ""):
                overrides[name] = new or None  # your own value; empty clears it
    answers = {}
    for name in WEB_ANSWER_FIELDS:
        key = f"a_{name}"
        if key in form:
            new = str(form[key]).strip()
            if new != _form_text(from_user.get(name)).strip():
                answers[name] = new or None
    duplicate = str(form.get("duplicate") or "")
    return {"company": company, "department": department, "create_missing": create, "overrides": overrides,
            "answers": answers,
            "recapture": duplicate.removeprefix("recapture:") if duplicate.startswith("recapture:") else None,
            "allow_duplicate": duplicate == "new", "replace_posting": bool(form.get("replace_posting"))}


def _profile_diff(public: dict | None, job: dict | None) -> dict | None:
    if not public or not job:
        return None
    pub_b = {b for ids in list(public["roles"].values()) + list(public["projects"].values()) for b in ids}
    job_b = {b for ids in list(job["roles"].values()) + list(job["projects"].values()) for b in ids}
    return {"added": sorted(job_b - pub_b), "removed": sorted(pub_b - job_b),
            "keywords": job.get("keywords", []), "gaps": job.get("gaps", [])}
