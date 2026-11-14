"""HTTP API (generated from the operation registry) and the read-only tracker website.

PRD A5: every operation in the registry becomes `POST /api/{user}/{operation}` with its typed
input, and the OpenAPI spec is generated from the same definitions. The website only calls
operations too; it never reads files or the database directly.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, Environment, FileSystemLoader

from . import __version__
from .errors import AppError, PermissionDenied
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

    for op in sorted(app.registry.values(), key=lambda o: o.name):
        api.add_api_route(
            f"/api/{{user}}/{op.name}", _endpoint(app, op), methods=["POST"], name=op.name,
            summary=op.summary, description=f"Permission: `{op.permission}`" + (" · writes" if op.writes else ""),
            tags=[op.permission.split(":")[0]],
        )
    for router in [*app.extensions.routers, *routers]:
        api.include_router(router)
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


# ---------------------------------------------------------------- website (read-only)

def _website(api: FastAPI, app: App) -> None:
    templates = web_templates(app)

    def page(request: Request, name: str, **kw):
        return templates.TemplateResponse(request, name, {"version": __version__, **kw})

    def run(user: str, op: str, payload: dict | None = None):
        _check_user(app, user)
        space = app.space(user)
        with space._lock:
            return invoke(app, context_for(app, user, None), op, payload or {})

    @api.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        users, problems = app.discover_users()
        return page(request, "index.html", users=users, folder_problems=problems)

    @api.get("/u/{user}/", response_class=HTMLResponse, include_in_schema=False)
    def user_home(request: Request, user: str):
        return page(request, "user.html", user=user, status=run(user, "space_status"), actions=run(user, "actions"))

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
        return page(request, "job.html", user=user, job=detail, docs=docs, snaps=snaps,
                    profile_diff=_profile_diff(public, profile))

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


def _profile_diff(public: dict | None, job: dict | None) -> dict | None:
    if not public or not job:
        return None
    pub_b = {b for ids in list(public["roles"].values()) + list(public["projects"].values()) for b in ids}
    job_b = {b for ids in list(job["roles"].values()) + list(job["projects"].values()) for b in ids}
    return {"added": sorted(job_b - pub_b), "removed": sorted(pub_b - job_b),
            "keywords": job.get("keywords", []), "gaps": job.get("gaps", [])}
