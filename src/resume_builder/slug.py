"""Slugs and job ids (PRD R1.3)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
JOB_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
JOB_ID_MAX = 80

# Company-name suffixes ignored when looking for near-duplicates (R11.4).
_ORG_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "gmbh"}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)


def is_slug(text: str) -> bool:
    return bool(SLUG_RE.match(text or ""))


def normalize_org_name(name: str) -> str:
    """Lowercase, strip punctuation and legal suffixes: 'Acme Corp.' -> 'acme'."""
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]
    while words and words[-1] in _ORG_SUFFIXES:
        words.pop()
    return " ".join(words)


def build_job_id(entered: date, company_slug: str, department_slug: str | None, existing: set[str]) -> str:
    """YYYY-MM-DD-<company>[-<department>], at most 80 characters, unique within the user.

    The department slug is shortened first when the id is too long. Duplicates get -2, -3, ...
    """
    prefix = entered.isoformat()
    base = f"{prefix}-{company_slug}"
    if department_slug:
        room = JOB_ID_MAX - len(base) - 1 - 3  # leave room for a "-NN" suffix
        dept = department_slug[: max(room, 0)].strip("-")
        if dept:
            base = f"{base}-{dept}"
    base = base[: JOB_ID_MAX - 3].strip("-")
    candidate = base
    n = 2
    while candidate in existing:
        candidate = f"{base}-{n}"
        n += 1
    return candidate
