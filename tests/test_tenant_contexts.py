"""Internal request contexts carry the App's tenant (issue #7)."""

import dataclasses
import shutil
import threading

import pytest

from resume_builder import server
from resume_builder import sync as sync_mod
from resume_builder.config import load_settings
from resume_builder.errors import PermissionDenied
from resume_builder.mcp import McpServer
from resume_builder.operations import invoke
from resume_builder.security import RequestContext
from resume_builder.space import App
from resume_builder.sync_ops import bootstrap

from .conftest import EXAMPLE, JOB, USER, data_file

TENANT = "tenant-a"


@pytest.fixture
def tenant_app(roots):
    a = App(load_settings(), tenant_id=TENANT)
    yield a
    a.close()


def test_context_fills_in_the_apps_tenant(tenant_app):
    ctx = tenant_app.context("system", USER)
    assert (ctx.actor, ctx.roles, ctx.channel, ctx.tenant_id) == ("system", ("system",), "bootstrap", TENANT)
    assert tenant_app.context("anonymous", USER).roles == ("viewer",)
    assert tenant_app.context("anonymous", USER).channel == "web"
    assert tenant_app.context("owner", USER).channel == "cli"
    assert tenant_app.context("agent", USER, "mcp").channel == "mcp"


def test_bootstrap_and_sync_in_another_tenant(tenant_app, roots):
    shutil.copytree(EXAMPLE, roots / "data" / USER, ignore=shutil.ignore_patterns(".schemas", "SYNC-STATUS.md"))
    report = bootstrap(tenant_app)
    assert report["users"][USER]["sync"]["ok"], report["users"][USER]["sync"]
    space = tenant_app.space(USER)
    assert space.repo.application_row(JOB)

    # A later edit, synced the way the watcher, the CLI and `rb mcp` do it.
    p = data_file(roots, f"jobs/{JOB}/application.yaml")
    p.write_text(p.read_text().replace("interest_level: 4", "interest_level: 5"))
    r = sync_mod.run_sync(space, tenant_app.context("system", USER))
    assert r.ok and r.results[0].result == "applied", [(x.rel, x.problems) for x in r.results]
    assert space.repo.get_application(JOB).interest_level == 5

    # Contexts from the local tenant are refused in this App, so the helper is what makes this work.
    with pytest.raises(PermissionDenied):
        invoke(tenant_app, RequestContext.owner(USER), "job_get", {"job_id": JOB})
    assert invoke(tenant_app, tenant_app.context("owner", USER), "job_get", {"job_id": JOB})


def test_bootstrap_creates_the_default_user_in_another_tenant(roots, monkeypatch):
    monkeypatch.setenv("RB_DEFAULT_USER", "sam-lee")
    app = App(load_settings(), tenant_id=TENANT)
    try:
        report = bootstrap(app)
        assert report["created_user"] == "sam-lee"
        assert report["users"]["sam-lee"]["sync"]["ok"]
    finally:
        app.close()


def test_watcher_and_mcp_sync_in_another_tenant(tenant_app, roots):
    shutil.copytree(EXAMPLE, roots / "data" / USER, ignore=shutil.ignore_patterns(".schemas", "SYNC-STATUS.md"))
    bootstrap(tenant_app)
    space = tenant_app.space(USER)
    p = data_file(roots, f"jobs/{JOB}/application.yaml")

    # The MCP server syncs before each tool call, then answers as the agent in this tenant.
    p.write_text(p.read_text().replace("interest_level: 4", "interest_level: 3"))
    out = McpServer(tenant_app, USER).call("job_get", {"job_id": JOB})
    assert not out.get("isError"), out
    assert space.repo.get_application(JOB).interest_level == 3

    # One pass of the file watcher.
    p.write_text(p.read_text().replace("interest_level: 3", "interest_level: 2"))
    stop = threading.Event()
    tenant_app.settings = dataclasses.replace(tenant_app.settings, sync_poll_seconds=0.01)
    t = threading.Thread(target=server.watch, args=(tenant_app, stop))
    t.start()
    try:
        for _ in range(500):
            if space.repo.get_application(JOB).interest_level == 2:
                break
            stop.wait(0.01)
    finally:
        stop.set()
        t.join(5)
    assert space.repo.get_application(JOB).interest_level == 2
