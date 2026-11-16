"""Content library operations (PRD 8.2). The agent may propose drafts; only the owner confirms."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..errors import Conflict, NotFound, ValidationFailed
from ..models import Bullet, ContentLibrary
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace
from ..validation import validate_library


class Empty(BaseModel):
    pass


@operation("library_get", "content:read", Empty, "The content library (archived items excluded).")
def library_get(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    return space.repo.load_library().model_dump(mode="json")


class ProposeBulletIn(BaseModel):
    parent_id: str = Field(description="Role or project id the bullet belongs to")
    bullet: Bullet


@operation("bullet_propose", "content:propose", ProposeBulletIn, "Propose a new bullet. Always saved as a draft.", writes=True)
def bullet_propose(space: UserSpace, ctx: RequestContext, data: ProposeBulletIn) -> dict:
    lib = space.repo.load_library(include_archived=False)
    bullet = data.bullet.model_copy(update={"status": "draft"})  # proposals are drafts, whoever sends them
    parent = next((r for r in lib.roles if r.id == data.parent_id), None) or next((p for p in lib.projects if p.id == data.parent_id), None)
    if parent is None:
        raise NotFound(f"No role or project `{data.parent_id}`.")
    if bullet.id in set(lib.all_ids()) | space.repo.archived_ids():
        raise Conflict(f"Id `{bullet.id}` is already used. Pick another.")
    parent.bullets.append(bullet)
    _save_library(space, ctx, lib)
    return {"proposed": bullet.id, "status": "draft", "next": f"The owner reviews it in base.yaml and confirms with `rb confirm {bullet.id}`."}


class ConfirmIn(BaseModel):
    id: str


@operation("item_confirm", "content:confirm", ConfirmIn,
           "Confirm a draft bullet, summary phrase, education, certification or skill group.", writes=True)
def item_confirm(space: UserSpace, ctx: RequestContext, data: ConfirmIn) -> dict:
    lib = space.repo.load_library()
    target = lib.all_bullets().get(data.id)
    for coll in (lib.summary_phrases, lib.education, lib.certifications, lib.skills):
        target = target or next((x for x in coll if x.id == data.id), None)
    if target is None:
        raise NotFound(_not_confirmable(lib, data.id))
    if data.id.startswith("example-"):
        raise ValidationFailed("Example entries can't be confirmed. Replace them with your own content.")
    target.status = "confirmed"
    _save_library(space, ctx, lib)
    return {"confirmed": data.id}


class ConfirmAllIn(BaseModel):
    dry_run: bool = Field(True, description="List what would be confirmed without changing anything. Set false to apply.")
    ids: list[str] | None = Field(None, description="Only these ids (as listed by a dry run), so nothing the user didn't see gets confirmed. null = every draft.")


def _drafts(lib: ContentLibrary) -> list[tuple[str, str, object]]:
    """Every draft item with a status, as (kind, label, item), in library order."""
    out: list[tuple[str, str, object]] = []
    out += [("summary phrase", s.text, s) for s in lib.summary_phrases if s.status == "draft"]
    for parent in [*lib.roles, *lib.projects]:
        out += [("bullet", b.text, b) for b in parent.bullets if b.status == "draft"]
    out += [("education", " ".join(x for x in (e.degree, e.field, e.institution) if x), e) for e in lib.education if e.status == "draft"]
    out += [("certification", c.name, c) for c in lib.certifications if c.status == "draft"]
    out += [("skill group", f"{g.name}: {', '.join(g.items)}", g) for g in lib.skills if g.status == "draft"]
    return out


@operation("library_confirm_all", "content:confirm", ConfirmAllIn,
           "Confirm every draft bullet, summary phrase, education, certification and skill group at once. Dry run by default.",
           writes=True)
def library_confirm_all(space: UserSpace, ctx: RequestContext, data: ConfirmAllIn) -> dict:
    lib = space.repo.load_library()
    drafts = _drafts(lib)
    skipped = [it.id for _, _, it in drafts if it.id.startswith("example-")]
    wanted = set(data.ids) if data.ids is not None else None
    chosen = [(kind, label, it) for kind, label, it in drafts
              if not it.id.startswith("example-") and (wanted is None or it.id in wanted)]
    items = [{"id": it.id, "kind": kind, "text": label} for kind, label, it in chosen]
    if not data.dry_run and chosen:
        for _, _, it in chosen:
            it.status = "confirmed"
        _save_library(space, ctx, lib)
    return {"applied": not data.dry_run and bool(items), "count": len(items), "items": items, "skipped_examples": skipped}


def _not_confirmable(lib: ContentLibrary, item_id: str) -> str:
    """Explain why `item_id` can't be confirmed, pointing at what can."""
    parent = next((r for r in lib.roles if r.id == item_id), None)
    kind = "role"
    if parent is None:
        parent = next((p for p in lib.projects if p.id == item_id), None)
        kind = "project"
    if parent is not None:
        drafts = [b.id for b in parent.bullets if b.status == "draft"]
        msg = f"`{item_id}` is a {kind}; {kind}s aren't confirmed, their bullets are. It shows on a resume once a profile picks one of its confirmed bullets."
        if drafts:
            msg += " Draft bullets to confirm: " + ", ".join(drafts) + "."
        else:
            msg += " All its bullets are already confirmed."
        return msg
    return (f"No item `{item_id}` in your content library. Only bullets, summary phrases, education, certifications "
            "and skill groups are confirmed.")


def _save_library(space: UserSpace, ctx: RequestContext, lib: ContentLibrary) -> None:
    problems = validate_library(lib)
    if problems:
        raise ValidationFailed("The content library has problems.", problems)
    with space.repo.transaction():
        space.repo.replace_library(lib, ctx.actor)
    space.changed("library")
