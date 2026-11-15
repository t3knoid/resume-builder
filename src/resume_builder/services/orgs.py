"""Organization registry (PRD 8.11): companies and departments, reused across jobs."""

from __future__ import annotations

from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from ..errors import Conflict, NotFound, ValidationFailed
from ..models import Company, Department, OrganizationRegistry
from ..operations import operation
from ..security import RequestContext
from ..slug import is_slug, normalize_org_name, slugify
from ..space import UserSpace
from ..validation import validate_registry


class Empty(BaseModel):
    pass


@operation("org_list", "organization:read", Empty, "All companies and departments.")
def org_list(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    return space.repo.load_registry().model_dump(mode="json")


def _names(entry) -> list[str]:
    return [entry.name, entry.slug, *entry.aliases]


SAME_THRESHOLD = 0.84     # this close counts as "the same organization" (R11.4)
SUGGEST_THRESHOLD = 0.6   # this close is worth offering in a pick list


def score_matches(entries, name: str, threshold: float = SAME_THRESHOLD) -> list[tuple[float, object]]:
    """(score, entry) for existing entries matching a name or alias, best first. Case, punctuation
    and Inc/LLC/Corp are ignored (R11.4); an exact name or alias match scores 1.0."""
    target = normalize_org_name(name)
    scored = []
    for e in entries:
        best = max(SequenceMatcher(None, target, normalize_org_name(n)).ratio() for n in _names(e))
        if target and any(normalize_org_name(n) == target for n in _names(e)):
            best = 1.0
        if best >= threshold:
            scored.append((best, e))
    return sorted(scored, key=lambda t: -t[0])


def find_matches(entries, name: str, threshold: float = SAME_THRESHOLD) -> list:
    """Existing entries matching a name or alias, ignoring case, punctuation and Inc/LLC/Corp (R11.4)."""
    return [e for _, e in score_matches(entries, name, threshold)]


class SuggestIn(BaseModel):
    name: str
    company: str | None = Field(None, description="Company slug, to suggest departments")


@operation("org_suggest", "organization:read", SuggestIn, "Existing companies (or departments) that match a name.")
def org_suggest(space: UserSpace, ctx: RequestContext, data: SuggestIn) -> list[dict]:
    reg = space.repo.load_registry()
    if data.company:
        comp = next((c for c in reg.companies if c.slug == data.company), None)
        entries = comp.departments if comp else []
    else:
        entries = reg.companies
    return [{"slug": e.slug, "name": e.name} for e in find_matches(entries, data.name, threshold=0.6)]


class AddCompanyIn(BaseModel):
    name: str
    slug: str | None = None
    aliases: list[str] = Field(default_factory=list)
    website: str | None = None
    force: bool = Field(False, description="Create even if a similar company exists")


@operation("company_add", "organization:write", AddCompanyIn, "Add a company to the registry.", writes=True)
def company_add(space: UserSpace, ctx: RequestContext, data: AddCompanyIn) -> dict:
    reg = space.repo.load_registry()
    slug = data.slug or slugify(data.name)
    if not is_slug(slug):
        raise ValidationFailed(f"Can't make a slug from `{data.name}`. Pass one with --slug.")
    if any(c.slug == slug for c in space.repo.load_registry(include_archived=True).companies):
        raise Conflict(f"Company `{slug}` already exists (it may be archived; restore it in organizations.yaml).")
    similar = find_matches(reg.companies, data.name)
    if similar and not data.force:
        raise Conflict("Did you mean " + ", ".join(f"`{c.slug}` ({c.name})" for c in similar) + "? Use that slug, or add with --force.")
    reg.companies.append(Company(slug=slug, name=data.name, aliases=data.aliases, website=data.website))
    _save(space, ctx, reg)
    return {"slug": slug}


class AddDeptIn(BaseModel):
    company: str
    name: str
    slug: str | None = None
    force: bool = False


@operation("department_add", "organization:write", AddDeptIn, "Add a department under a company.", writes=True)
def department_add(space: UserSpace, ctx: RequestContext, data: AddDeptIn) -> dict:
    reg = space.repo.load_registry()
    comp = next((c for c in reg.companies if c.slug == data.company), None)
    if comp is None:
        raise NotFound(f"No company `{data.company}`.")
    slug = data.slug or slugify(data.name)
    if any(d.slug == slug for d in comp.departments):
        raise Conflict(f"Department `{slug}` already exists under `{comp.slug}`.")
    similar = find_matches(comp.departments, data.name)
    if similar and not data.force:
        raise Conflict("Did you mean " + ", ".join(f"`{d.slug}` ({d.name})" for d in similar) + "? Use that slug, or add with --force.")
    comp.departments.append(Department(slug=slug, name=data.name))
    _save(space, ctx, reg)
    return {"company": comp.slug, "slug": slug}


class RenameIn(BaseModel):
    company: str
    department: str | None = None
    new_name: str


@operation("org_rename", "organization:write", RenameIn, "Rename a company or department; the old name becomes an alias.", writes=True)
def org_rename(space: UserSpace, ctx: RequestContext, data: RenameIn) -> dict:
    reg = space.repo.load_registry()
    comp = next((c for c in reg.companies if c.slug == data.company), None)
    if comp is None:
        raise NotFound(f"No company `{data.company}`.")
    target = comp
    if data.department:
        target = next((d for d in comp.departments if d.slug == data.department), None)
        if target is None:
            raise NotFound(f"No department `{data.department}` under `{data.company}`.")
    if target.name not in target.aliases:
        target.aliases.append(target.name)
    target.name = data.new_name
    _save(space, ctx, reg)
    return {"renamed": target.slug, "name": data.new_name, "note": "Job ids keep their original slug (R1.3)."}


class MergeIn(BaseModel):
    keep: str = Field(description="Slug of the company that survives")
    merge: str = Field(description="Slug of the duplicate to merge into it")


@operation("org_merge", "organization:merge", MergeIn, "Merge a duplicate company into another.", writes=True)
def org_merge(space: UserSpace, ctx: RequestContext, data: MergeIn) -> dict:
    reg = space.repo.load_registry()
    keep = next((c for c in reg.companies if c.slug == data.keep), None)
    dup = next((c for c in reg.companies if c.slug == data.merge), None)
    if not keep or not dup:
        raise NotFound("Both companies must exist.")
    for alias in [dup.name, *dup.aliases]:
        if alias not in keep.aliases and alias != keep.name:
            keep.aliases.append(alias)
    existing = {d.slug for d in keep.departments}
    keep.departments += [d for d in dup.departments if d.slug not in existing]
    reg.companies = [c for c in reg.companies if c.slug != dup.slug]
    moved = []
    with space.repo.transaction():
        space.repo.replace_registry(reg, ctx.actor)
        for job_id in space.repo.job_ids():
            app = space.repo.get_application(job_id)
            if app.company == dup.slug:
                app.company = keep.slug
                if app.department not in {d.slug for d in keep.departments}:
                    app.department = None
                stage = space.repo.application_row(job_id)["stage"]
                space.repo.save_application(app, stage, ctx.actor)
                moved.append(job_id)
    space.changed("registry")
    for job_id in moved:
        space.changed("application", job_id)
    return {"kept": keep.slug, "merged": dup.slug, "applications_moved": moved}


def _save(space: UserSpace, ctx: RequestContext, reg: OrganizationRegistry) -> None:
    problems = validate_registry(reg)
    if problems:
        raise ValidationFailed("The registry has problems.", problems)
    with space.repo.transaction():
        space.repo.replace_registry(reg, ctx.actor)
    space.changed("registry")
