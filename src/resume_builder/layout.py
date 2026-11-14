"""The user folder layout (PRD 8.1) as path labels: no file access.

Services use these only to name a working copy in messages and validation problems. Only the sync
adapter turns them into files.
"""

from __future__ import annotations


def doc_rel(job_id: str, kind: str, slug: str = "") -> str:
    if kind == "answer":
        return f"jobs/{job_id}/answers/{slug}.md"
    return f"jobs/{job_id}/{'fit-report.md' if kind == 'fit_report' else 'cover-letter.md'}"


def profile_rel(key: str) -> str:
    return "profiles/public.yaml" if key == "public" else f"jobs/{key}/profile.yaml"


def application_rel(job_id: str) -> str:
    return f"jobs/{job_id}/application.yaml"
