"""RBAC readiness (PRD B2, B3, B7, B9)."""

import pytest

from resume_builder.errors import PermissionDenied
from resume_builder.operations import REGISTRY, invoke
from resume_builder.security import PERMISSIONS, ROLE_PERMISSIONS, DenyAllPolicy, RequestContext, RolePolicy

from .conftest import JOB, USER


def test_every_operation_declares_a_known_permission():
    assert REGISTRY, "operations should be registered"
    for op in REGISTRY.values():
        assert op.permission in PERMISSIONS, op.name


def test_deny_all_blocks_every_operation(sample, owner):
    sample.app.policy = DenyAllPolicy()
    for name in REGISTRY:
        with pytest.raises(PermissionDenied):
            invoke(sample.app, owner, name, {})


def test_read_only_role_blocks_every_write(sample):
    viewer = RequestContext(actor="viewer", roles=("viewer",), user_id=USER)
    for op in REGISTRY.values():
        if op.writes:
            with pytest.raises(PermissionDenied):
                invoke(sample.app, viewer, op.name, {})


def test_agent_cannot_confirm_approve_sync_or_publish(sample, agent):
    for name, payload in [
        ("item_confirm", {"id": "fab-ci-pipeline"}),
        ("library_confirm_all", {"dry_run": False}),
        ("document_approve", {"job_id": JOB, "kind": "answer"}),
        ("sync", {}),
        ("publish_public", {}),
        ("event_append", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note"}}),
        ("org_merge", {"keep": "northwind-labs", "merge": "contoso-health"}),
        ("snapshot_delete", {"snapshot_id": "sn-x"}),
        ("token_issue", {"actor": "owner"}),
    ]:
        with pytest.raises(PermissionDenied):
            invoke(sample.app, agent, name, payload)


def test_agent_can_propose(sample, agent):
    out = invoke(sample.app, agent, "event_propose", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "Recruiter replied"}})
    assert out["draft_id"]


def test_policy_is_per_app(sample, owner):
    """Two Apps in one process don't share a policy (issue #6)."""
    from resume_builder.config import load_settings
    from resume_builder.space import App

    other = App(load_settings(), policy=DenyAllPolicy())
    try:
        with pytest.raises(PermissionDenied):
            invoke(other, owner, "job_intake_proposals", {})
        assert isinstance(sample.app.policy, RolePolicy)
        invoke(sample.app, owner, "job_intake_proposals", {})
    finally:
        other.close()


def test_default_policy_uses_the_apps_role_table(sample):
    """The default RolePolicy reads this App's registry, so plugin grants added later apply."""
    app = sample.app
    assert app.policy.role_permissions is app.registry.role_permissions
    app.registry.add_permission("demo:read", roles=("viewer",))
    viewer = RequestContext(actor="viewer", roles=("viewer",), user_id=USER)
    assert app.policy.allows(viewer, "demo:read", USER)


def test_actor_cannot_act_in_another_users_space(sample):
    other = RequestContext.owner("someone-else")
    assert not RolePolicy().allows(other, "application:read", USER)


def test_roles_are_configuration():
    assert ROLE_PERMISSIONS["agent"] < PERMISSIONS
    assert "letter:approve" not in ROLE_PERMISSIONS["agent"]
