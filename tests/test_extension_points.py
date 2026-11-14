"""Extension points (docs/ARCHITECTURE.md): an outside package extends the core without patching it.

The fake plugin below uses only the public seams: an operation with its own permission, a router,
a website template override, an in-memory Storage, a SecretStore and a change listener.
"""

from __future__ import annotations

import contextlib
import sqlite3

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel

from resume_builder.changes import Change
from resume_builder.config import load_settings
from resume_builder.errors import NotFound, PermissionDenied, ValidationFailed
from resume_builder.mcp import McpServer, tools_for
from resume_builder.models import Company, OrganizationRegistry
from resume_builder.operations import REGISTRY, invoke
from resume_builder.secretstore import DictSecretStore
from resume_builder.security import RequestContext
from resume_builder.services.render import contact_values, template_dir
from resume_builder.space import App, build_app
from resume_builder.web.app import create_app, get_app

from .conftest import USER


# ---------------------------------------------------------------- the fake plugin

class GreetIn(BaseModel):
    name: str = "world"


class FakePlugin:
    """What an outside package would ship, registered only through the extension points."""

    def register(self, ext) -> None:
        ext.add_permission("greeting:read", roles=("owner", "agent"))

        @ext.operation("greeting_say", "greeting:read", GreetIn, "Say hello (plugin operation).")
        def greeting_say(space, ctx, data: GreetIn) -> dict:
            return {"hello": data.name, "user": space.user, "actor": ctx.actor}

        router = APIRouter(prefix="/plugin")

        @router.get("/ping")
        def ping(app: App = Depends(get_app)) -> dict:
            return {"pong": True, "has_greeting": "greeting_say" in app.registry}

        ext.add_router(router)


# ---------------------------------------------------------------- an in-memory Storage

class MemoryStorage:
    """Just enough of storage.Storage for the organization operations, in memory."""

    def __init__(self):
        self.calls: list[str] = []
        self.registry = OrganizationRegistry(companies=[Company(slug="contoso-health", name="Contoso Health")])
        self.audit_log: list[tuple] = []

    def __getattr__(self, name):  # any method the test didn't expect is a failure, not a silent pass
        raise AssertionError(f"unexpected storage call: {name}")

    def transaction(self):
        self.calls.append("transaction")
        return contextlib.nullcontext()

    def migrate(self, backup_fn=None):
        self.calls.append("migrate")
        return []

    def schema_version(self):
        return 1

    def close(self):
        pass

    def load_registry(self, include_archived=False):
        self.calls.append("load_registry")
        return self.registry.model_copy(deep=True)

    def replace_registry(self, reg, actor):
        self.calls.append("replace_registry")
        self.registry = reg

    def audit(self, actor, channel, request_id, operation, target, result):
        self.audit_log.append((actor, channel, operation, target, result))


@pytest.fixture
def no_sqlite(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("SQLite must not be used when a Storage is injected")
    monkeypatch.setattr(sqlite3, "connect", refuse)


# ---------------------------------------------------------------- tests

def test_plugin_operation_and_router_appear_in_api_and_mcp(sample, owner):
    api = create_app(sample.app, plugins=[FakePlugin()])
    client = TestClient(api)

    assert client.get("/plugin/ping").json() == {"pong": True, "has_greeting": True}
    assert f"/api/{{user}}/greeting_say" in client.get("/openapi.json").json()["paths"]

    # Behind the usual permission check: no token means the read-only viewer role, which lacks it.
    denied = client.post(f"/api/{USER}/greeting_say", json={"name": "Alex"})
    assert denied.status_code == 403
    token = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]
    ok = client.post(f"/api/{USER}/greeting_say", json={"name": "Alex"}, headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200 and ok.json() == {"hello": "Alex", "user": USER, "actor": "agent"}

    agent_tools = {t["name"] for t in tools_for(sample.app.registry, "agent")}
    viewer_tools = {t["name"] for t in tools_for(sample.app.registry, "viewer")}
    assert "greeting_say" in agent_tools
    assert "greeting_say" not in viewer_tools
    reply = McpServer(sample.app, USER).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                "params": {"name": "greeting_say", "arguments": {"name": "MCP"}}})
    assert not reply["result"]["isError"] and '"hello": "MCP"' in reply["result"]["content"][0]["text"]


def test_plugins_stay_in_their_own_app(roots):
    with_plugin = App(load_settings(), plugins=[FakePlugin()])
    plain = App(load_settings())
    try:
        assert "greeting_say" in with_plugin.registry and "greeting:read" in with_plugin.registry.permissions
        assert "greeting_say" not in plain.registry and "greeting_say" not in REGISTRY
        assert "greeting:read" not in REGISTRY.permissions
    finally:
        with_plugin.close()
        plain.close()


def test_plugin_cannot_use_an_undeclared_permission(roots):
    def bad(ext):
        ext.operation("sneaky", "sneaky:write", GreetIn, "Not declared.")(lambda space, ctx, data: None)

    with pytest.raises(ValueError, match="unknown permission"):
        App(load_settings(), plugins=[bad])


def test_installed_plugins_load_from_entry_points(roots, monkeypatch):
    class FakeEntryPoint:
        name, value = "fake", "tests:FakePlugin"

        def load(self):
            return FakePlugin()

    monkeypatch.setattr("resume_builder.plugins.entry_points", lambda group: [FakeEntryPoint()] if group == "resume_builder.plugins" else [])
    app = build_app(load_settings(), load_entry_points=True)
    try:
        assert "greeting_say" in app.registry and app.extensions.loaded == ["fake"]
    finally:
        app.close()
    monkeypatch.setenv("RB_PLUGINS", "off")
    app = build_app(load_settings())
    try:
        assert "greeting_say" not in app.registry
    finally:
        app.close()


def test_injected_storage_is_used_instead_of_sqlite(roots, no_sqlite):
    store = MemoryStorage()
    changes: list[Change] = []
    app = App(load_settings(), storage_factory=lambda app, user: store,
              change_listeners=[lambda space, change: changes.append(change) or False])
    owner = RequestContext.owner(USER)

    assert invoke(app, owner, "org_list", {})["companies"][0]["slug"] == "contoso-health"
    invoke(app, owner, "company_add", {"name": "Fabrikam"})

    assert [c.slug for c in store.registry.companies] == ["contoso-health", "fabrikam"]
    assert "replace_registry" in store.calls and "transaction" in store.calls
    assert ("owner", "cli", "company_add", "", "ok") in store.audit_log
    assert changes == [Change("registry")]  # the listener heard it; no file adapter was involved
    assert not (roots / "data" / USER).exists()


def test_website_template_override_is_used(sample, tmp_path):
    override = tmp_path / "web-templates"
    override.mkdir()
    (override / "base.html").write_text(
        "<!doctype html><title>{% block title %}{% endblock %}</title>"
        "<p id='host-banner'>Custom host header</p><main>{% block body %}{% endblock %}</main>", encoding="utf-8")
    client = TestClient(create_app(sample.app, template_dirs=[override]))
    page = client.get("/")
    assert page.status_code == 200
    assert "Custom host header" in page.text
    assert USER in page.text  # index.html itself still comes from the package


def test_document_templates_can_add_versions_but_not_replace_released_ones(sample, tmp_path):
    extra = tmp_path / "doc-templates"
    (extra / "resume" / "classic" / "99").mkdir(parents=True)
    sample.app.extensions.add_document_templates(extra)
    path, version = template_dir(sample, "resume", "classic", 99)
    assert version == 99 and path == extra / "resume" / "classic" / "99"

    (extra / "resume" / "classic" / "1").mkdir()
    with pytest.raises(ValidationFailed, match="released"):
        template_dir(sample, "resume", "classic", 1)


def test_secret_store_supplies_contact_details(roots):
    app = App(load_settings(), secrets=DictSecretStore({"RB_CONTACT_EMAIL": "alex@example.com"}))
    try:
        values, missing = contact_values(app.space(USER))
        assert values == {"email": "alex@example.com"}
        assert missing == ["RB_CONTACT_PHONE"]
    finally:
        app.close()


def test_policy_refuses_other_tenants(roots, no_sqlite):
    app = App(load_settings(), tenant_id="tenant-a", storage_factory=lambda app, user: MemoryStorage(),
              change_listeners=[])
    with pytest.raises(PermissionDenied):
        invoke(app, RequestContext.owner(USER), "org_list", {})  # the local tenant, not tenant-a
    with pytest.raises(PermissionDenied):
        invoke(app, RequestContext.owner(USER, tenant_id="tenant-b"), "org_list", {})
    assert invoke(app, RequestContext.owner(USER, tenant_id="tenant-a"), "org_list", {})["companies"]


def test_unknown_operation_is_not_found(sample, owner):
    with pytest.raises(NotFound):
        invoke(sample.app, owner, "greeting_say", {})


def test_local_file_adapter_writes_back_on_changes(sample, owner, roots):
    """The default change listener: a new job seeds posting.md; a registry change refreshes editor schemas."""
    import json

    job = invoke(sample.app, owner, "job_new", {"company": "Contoso Health"})["job_id"]
    folder = roots / "data" / USER / "jobs" / job
    assert (folder / "application.yaml").is_file()
    assert "# Job posting" in (folder / "posting.md").read_text(encoding="utf-8")

    invoke(sample.app, owner, "company_add", {"name": "Tailspin Toys"})
    schema = json.loads((roots / "data" / USER / ".schemas" / "application.schema.json").read_text(encoding="utf-8"))
    assert "tailspin-toys" in schema["properties"]["company"]["enum"]
