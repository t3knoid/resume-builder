"""Fit reports, cover letters and application answers, with honesty checks (PRD 8.9).

Letters and answers draw evidence only from the job's tailored profile. Every claim sentence
ends with a `[cite: bullet-id, ...]` marker. The agent's drafts must pass every check; the
owner's own edits only produce recorded warnings (D10).
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from ..layout import doc_rel
from ..errors import NotFound, Problem, ValidationFailed
from ..models import DocumentDraft, word_count
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace

CITE_RE = re.compile(r"\[cite:\s*([^\]]*)\]", re.I)
PLACEHOLDER = "[MOTIVATION NEEDED]"
NUMBER_RE = re.compile(r"(?<![A-Za-z])\$?\d[\d,]*(?:\.\d+)?%?")


def strip_citations(text: str) -> str:
    text = re.sub(r"\s*\[cite:[^\]]*\]", "", text, flags=re.I)
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", text)


def split_sentences(text: str) -> list[tuple[str, list[str]]]:
    """Sentences with the ids cited at their end."""
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    body = re.sub(r"([.!?])\s*(\[cite:[^\]]*\])", r" \2\1", body, flags=re.I)
    out = []
    for para in re.split(r"\n\s*\n", body):
        para = " ".join(para.split())
        for sent in re.split(r"(?<=[.!?])\s+", para):
            if not sent.strip():
                continue
            ids = []
            for m in CITE_RE.finditer(sent):
                ids += [x.strip() for x in m.group(1).split(",") if x.strip()]
            out.append((strip_citations(sent).strip(), ids))
    return out


def _terms(space: UserSpace) -> set[str]:
    lib = space.repo.load_library()
    terms = {t.replace("-", " ") for t in lib.all_tags()} | {t for t in lib.all_tags()}
    for g in lib.skills:
        terms |= {i.lower() for i in g.items}
    return {t.lower() for t in terms if len(t) >= 3}


def _norm_num(s: str) -> str:
    return s.replace(",", "").replace("$", "")


def check_document(space: UserSpace, job_id: str, kind: str, text: str, owner_edit: bool) -> list[Problem]:
    """Honesty and style checks for a letter or answer (R9.9–R9.11)."""
    sev = "warning" if owner_edit else "error"
    rel = doc_rel(job_id, kind, "why-here" if kind == "answer" else "")
    problems: list[Problem] = []
    profile = space.repo.get_profile(job_id)
    if profile is None:
        return [Problem("Tailor the resume first: this job has no profile yet, and letters may only use its content.", file=rel, severity=sev)]
    lib = space.repo.load_library()
    bullets = lib.all_bullets()
    phrases = {s.id: s for s in lib.summary_phrases}
    allowed = profile.bullet_ids() | set(profile.summary)  # earlier experience is on the resume too
    terms = _terms(space)
    # Motivation may use the user's own words from interest_notes without a citation (R9.3).
    notes = (space.repo.get_application(job_id).interest_notes or "").lower()

    for sentence, cited in split_sentences(text):
        if not sentence:
            continue
        lower = sentence.lower()
        numbers = [_norm_num(n) for n in NUMBER_RE.findall(sentence)]
        mentioned = sorted(t for t in terms if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", lower))
        if not cited:
            mentioned = [t for t in mentioned if not re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", notes)]
            if numbers or mentioned:
                what = ", ".join(numbers + mentioned)
                problems.append(Problem(f'Claim without a citation ("{sentence[:80]}…" mentions {what}). End the sentence with [cite: bullet-id].',
                                        file=rel, severity=sev))
            continue
        sources = []
        for cid in cited:
            if cid not in allowed:
                problems.append(Problem(f"`{cid}` is cited but isn't on this job's tailored resume (profile.yaml).", file=rel, severity=sev))
                continue
            item = bullets.get(cid) or phrases.get(cid)
            if item is None:
                problems.append(Problem(f"`{cid}` doesn't exist in your content library.", file=rel, severity=sev))
                continue
            if item.status != "confirmed":
                problems.append(Problem(f"`{cid}` is still a draft; confirm it before citing it.", file=rel, severity=sev))
            sources.append(" ".join([item.text, getattr(item, "metric", None) or "", " ".join(item.tags)]).lower())
        evidence = " ".join(sources)
        evidence_numbers = {_norm_num(x) for x in NUMBER_RE.findall(evidence)}  # whole numbers: "5" must not match "25"
        for n in numbers:
            if n not in evidence_numbers:
                problems.append(Problem(f'The number {n} in "{sentence[:60]}…" isn\'t in the cited bullets. Use the exact figure from base.yaml.',
                                        file=rel, severity=sev))
        for t in mentioned:
            if t not in evidence and t.replace(" ", "-") not in evidence:
                problems.append(Problem(f'"{t}" is mentioned but isn\'t in the cited bullets. Cite the bullet that shows it, or remove it.',
                                        file=rel, severity=sev))

    s = space.user_settings.writing
    words = word_count(strip_citations(text))
    if kind == "answer" and words > s.answer_max_words:
        problems.append(Problem(f"{words} words; the limit is {s.answer_max_words}.", file=rel, severity=sev))
    if kind == "cover_letter" and not (s.cover_letter_min_words <= words <= s.cover_letter_max_words):
        problems.append(Problem(f"{words} words; aim for {s.cover_letter_min_words}–{s.cover_letter_max_words}.", file=rel, severity=sev))
    if kind == "answer" and re.search(r"^\s*(#|[-*] )", text, re.M):
        problems.append(Problem("Answers are plain prose: no headings or bullet points.", file=rel, severity=sev))
    for phrase in s.banned_phrases:
        if phrase.lower() in text.lower():
            problems.append(Problem(f'Uses the banned phrase "{phrase}". Say what you actually did instead.', file=rel, severity=sev))
    if PLACEHOLDER in text:
        problems.append(Problem(f"{PLACEHOLDER} is still in the text. Add your interest notes in application.yaml, then write this part.",
                                file=rel, severity="warning"))
    return problems


class DocIn(DocumentDraft):
    job_id: str


@operation("document_propose", "letter:propose", DocIn, "Save a draft fit report, cover letter or answer.", writes=True)
def document_propose(space: UserSpace, ctx: RequestContext, data: DocIn) -> dict:
    if not space.repo.application_row(data.job_id):
        raise NotFound(f"No job `{data.job_id}`.")
    slug = (data.slug or "why-here") if data.kind == "answer" else ""
    question = data.question or ("Why do you want to work here?" if slug == "why-here" else None)
    if data.kind == "answer" and not question:
        raise ValidationFailed("An answer needs the `question` it answers.")
    findings = [] if data.kind == "fit_report" else check_document(space, data.job_id, data.kind, data.text, owner_edit=ctx.actor == "owner")
    errors = [p for p in findings if p.severity == "error"]
    if errors:
        raise ValidationFailed("The draft didn't pass the honesty checks.", findings)
    text = data.text.strip() + "\n"
    with space.repo.transaction():
        space.repo.save_document(data.job_id, data.kind, slug, text, "draft", ctx.actor, question=question,
                                 warnings=[p.to_dict() for p in findings])
    written = space.changed("document", data.job_id, doc_kind=data.kind, slug=slug)
    return {"job_id": data.job_id, "kind": data.kind, "slug": slug, "status": "draft",
            "warnings": [p.to_dict() for p in findings], "file": doc_rel(data.job_id, data.kind, slug), "file_updated": written}


class DocKeyIn(BaseModel):
    job_id: str
    kind: str = Field(pattern="^(fit_report|cover_letter|answer)$")
    slug: str | None = None


def _key(data: DocKeyIn) -> tuple[str, str]:
    return data.kind, ((data.slug or "why-here") if data.kind == "answer" else "")


@operation("document_get", "letter:read", DocKeyIn, "One document with its status and warnings.")
def document_get(space: UserSpace, ctx: RequestContext, data: DocKeyIn) -> dict:
    kind, slug = _key(data)
    row = space.repo.get_document(data.job_id, kind, slug)
    if row is None:
        raise NotFound(f"No {kind} for `{data.job_id}`.")
    return {**dict(row), "warnings": json.loads(row["warnings"])}


class JobIn(BaseModel):
    job_id: str


@operation("document_list", "letter:read", JobIn, "All documents for a job.")
def document_list(space: UserSpace, ctx: RequestContext, data: JobIn) -> list[dict]:
    return [{"kind": r["kind"], "slug": r["slug"], "status": r["status"], "question": r["question"],
             "approved_snapshot": r["approved_snapshot"], "updated_at": r["updated_at"], "updated_by": r["updated_by"]}
            for r in space.repo.list_documents(data.job_id)]


@operation("document_approve", "letter:approve", DocKeyIn, "Approve a cover letter or answer; saves its snapshot.", writes=True)
def document_approve(space: UserSpace, ctx: RequestContext, data: DocKeyIn) -> dict:
    kind, slug = _key(data)
    if kind == "fit_report":
        raise ValidationFailed("Fit reports are notes for you; they don't need approving.")
    row = space.repo.get_document(data.job_id, kind, slug)
    if row is None:
        raise NotFound(f"No {kind} for `{data.job_id}`.")
    if PLACEHOLDER in row["text"]:
        raise ValidationFailed(f"Replace {PLACEHOLDER} with your own words before approving.")
    findings = check_document(space, data.job_id, kind, row["text"], owner_edit=True)
    from .render import create_snapshot
    sid = create_snapshot(space, ctx, data.job_id, kind, slug=slug, reason="approved")
    with space.repo.transaction():
        space.repo.save_document(data.job_id, kind, slug, row["text"], "approved", ctx.actor,
                                 warnings=[p.to_dict() for p in findings], approved_snapshot=sid)
    space.changed("document", data.job_id, doc_kind=kind, slug=slug)
    return {"approved": kind, "snapshot_id": sid, "warnings_recorded": [p.to_dict() for p in findings],
            "next": f"Export it with `rb regenerate {sid}` (into output/). When you apply, add an `applied` event listing {sid}."}
