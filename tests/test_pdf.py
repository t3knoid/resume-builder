"""Byte-for-byte PDF regeneration (PRD R12.7). Runs where WeasyPrint is installed (the Docker image)."""

from pathlib import Path

import pytest

from resume_builder.operations import invoke
from resume_builder.services.render import export

from .conftest import JOB

weasyprint = pytest.importorskip("weasyprint")


def test_pdf_regenerates_byte_for_byte(sample, owner):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    first, _ = export(sample, sid, "pdf")
    a = Path(first).read_bytes()
    Path(first).unlink()
    second, _ = export(sample, sid, "pdf")
    assert a[:4] == b"%PDF"
    assert a == Path(second).read_bytes()


def test_letter_pdf(sample, owner):
    sid = invoke(sample.app, owner, "document_approve", {"job_id": JOB, "kind": "answer"})["snapshot_id"]
    path, _ = export(sample, sid, "pdf")
    assert Path(path).read_bytes()[:4] == b"%PDF"
