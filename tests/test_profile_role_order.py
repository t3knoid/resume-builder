"""The app puts a profile's roles newest first itself, whatever order the keys arrive in (R4.4).

JSON doesn't guarantee the order of an object's keys, and some MCP clients sort them, so a profile's
`roles` mapping can't carry the order. The library knows each role's dates.
"""

import pytest

from resume_builder import sync as sync_mod
from resume_builder.operations import invoke
from resume_builder.security import RequestContext

from .conftest import JOB, USER, data_file

NEWEST_FIRST = ["role-tailspin", "role-fabrikam"]


@pytest.fixture
def newest_sorts_last(sample, owner):
    """Make role-tailspin the newest role, so its id sorts after the older role's."""
    lib = sample.repo.load_library().model_dump(mode="json")
    for r in lib["roles"]:
        if r["id"] == "role-tailspin":
            r["start"], r["end"] = "2023-01", "present"
        if r["id"] == "role-fabrikam":
            r["start"], r["end"] = "2019-04", "2022-12"
    invoke(sample.app, owner, "library_replace", {"library": lib})
    return sample


def _profile(space, order):
    p = space.repo.get_profile(JOB).model_dump(mode="json")
    p["roles"] = {r: p["roles"][r] for r in order}
    return p


def _file_order(roots, rel, names):
    text = data_file(roots, rel).read_text(encoding="utf-8")
    return sorted(names, key=text.index)


@pytest.mark.parametrize("sent", [NEWEST_FIRST, list(reversed(NEWEST_FIRST))])
def test_any_order_sent_is_saved_newest_first(newest_sorts_last, agent, roots, sent):
    space = newest_sorts_last
    out = invoke(space.app, agent, "profile_write", {"key": JOB, "profile": _profile(space, sent)})
    assert out["saved"] == JOB
    assert list(space.repo.get_profile(JOB).roles) == NEWEST_FIRST
    # The working copy shows the same order, and syncing it changes nothing.
    assert _file_order(roots, f"jobs/{JOB}/profile.yaml", ["role-tailspin:", "role-fabrikam:"]) == ["role-tailspin:", "role-fabrikam:"]
    assert sync_mod.run_sync(space, RequestContext.system(USER)).results == []


def test_a_file_edited_out_of_order_is_applied_and_shown_newest_first(newest_sorts_last, roots):
    space = newest_sorts_last
    p = data_file(roots, f"jobs/{JOB}/profile.yaml")
    text = p.read_text(encoding="utf-8")
    # The sample file lists role-fabrikam first; after the date change that's oldest first.
    assert text.index("role-fabrikam:") < text.index("role-tailspin:")
    p.write_text("# my note\n" + text, encoding="utf-8")
    r = next(x for x in sync_mod.run_sync(space, RequestContext.system(USER)).results if x.rel.endswith("profile.yaml"))
    assert r.result == "applied", r.problems
    assert list(space.repo.get_profile(JOB).roles) == NEWEST_FIRST
    text = p.read_text(encoding="utf-8")
    assert "# my note" in text and text.index("role-tailspin:") < text.index("role-fabrikam:")


def test_resume_shows_roles_newest_first(newest_sorts_last, owner):
    from resume_builder.services.render import resolve_resume

    space = newest_sorts_last
    stored = space.repo.get_profile(JOB)  # saved before the dates changed: oldest first now
    assert list(stored.roles) == list(reversed(NEWEST_FIRST))
    sections, _, _ = resolve_resume(space.repo.load_library(), stored, preview=True)
    assert [r["id"] for r in sections["roles"]] == NEWEST_FIRST
