"""Tags that stand for more, declared by the user in user.yaml (implied_tags).

A bullet tagged with a key also counts as having the listed tags when matching postings and checking
letters. Only the user declares these; the stored library and base.yaml never change.
"""

import pytest

from resume_builder.errors import ValidationFailed
from resume_builder.models import ContentLibrary
from resume_builder.operations import invoke
from resume_builder.services.documents import check_document

from .conftest import JOB, data_file


def _set_implied(space, owner, implied):
    settings = space.user_settings.model_dump(mode="json")
    settings["implied_tags"] = implied
    invoke(space.app, owner, "settings_replace", {"settings": settings})


def test_tags_are_expanded_in_a_copy_and_follow_chains(sample):
    lib = sample.repo.load_library()
    expanded = lib.with_implied_tags({"docker": ["containers"], "containers": ["oci"]})
    assert expanded.all_bullets()["tail-docker"].tags == ["docker", "automation", "containers", "oci"]
    assert lib.all_bullets()["tail-docker"].tags == ["docker", "automation"]  # the original is untouched
    assert lib.with_implied_tags({}) is lib


def test_the_stored_library_and_base_yaml_never_change(sample, owner, roots):
    before = data_file(roots, "base.yaml").read_text(encoding="utf-8")
    _set_implied(sample, owner, {"docker": ["containers"]})
    assert "containers" not in invoke(sample.app, owner, "library_get", {})["roles"][1]["bullets"][0]["tags"]
    assert data_file(roots, "base.yaml").read_text(encoding="utf-8") == before
    assert "containers" in sample.matching_library().all_bullets()["tail-docker"].tags


def test_profile_keywords_may_use_an_implied_tag(sample, owner, agent):
    profile = sample.repo.get_profile(JOB).model_dump(mode="json")
    profile["keywords"] = [*profile["keywords"], "containers"]
    with pytest.raises(ValidationFailed) as exc:
        invoke(sample.app, agent, "profile_write", {"key": JOB, "profile": profile})
    assert any("`containers` is not a tag" in p.message for p in exc.value.problems)
    _set_implied(sample, owner, {"docker": ["containers"]})
    assert invoke(sample.app, agent, "profile_write", {"key": JOB, "profile": profile})["saved"] == JOB


def test_letter_check_counts_implied_tags_of_cited_bullets(sample, owner):
    _set_implied(sample, owner, {"docker": ["containers"]})
    shown = "I packaged services as containers. [cite: tail-docker]"
    not_shown = "I packaged services as containers. [cite: fab-ci-pipeline]"
    assert not [p for p in check_document(sample, JOB, "answer", shown, owner_edit=False) if "containers" in p.message]
    problems = check_document(sample, JOB, "answer", not_shown, owner_edit=False)
    assert any('"containers" is mentioned' in p.message for p in problems)


def test_brief_explains_and_shows_implied_tags(sample, owner, agent):
    _set_implied(sample, owner, {"docker": ["containers"]})
    brief = invoke(sample.app, agent, "agent_brief", {"job_id": JOB})
    assert "docker also means containers" in brief
    assert "`tail-docker` [docker, automation, containers]" in brief


def test_implied_tags_must_be_tags():
    from pydantic import ValidationError

    from resume_builder.models import UserSettings

    with pytest.raises(ValidationError):
        UserSettings(display_name="Alex", implied_tags={"REST APIs": ["json"]})
    assert ContentLibrary().with_implied_tags({"a": ["b"]}).all_tags() == set()
