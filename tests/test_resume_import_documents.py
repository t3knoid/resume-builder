"""Importing Word (.docx) and PDF resumes (issue #39, PRD R2.12).

The documents are made in the test from the same fictional resume as the other formats:
Word files with tests/docx_builder.py, PDFs with WeasyPrint (those tests skip without it).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from resume_builder import resume_import, resume_readers
from resume_builder.cli import main
from resume_builder.errors import ValidationFailed
from resume_builder.operations import invoke

from .conftest import JOB, USER, data_file
from .docx_builder import docx_from_lines
from .test_resume_import import assert_reads_the_fixture, text_of

def resume_docx() -> bytes:
    return docx_from_lines(text_of("resume.md"))


def resume_pdf() -> bytes:
    wp = pytest.importorskip("weasyprint")
    return wp.HTML(string=text_of("resume.html")).write_pdf()


def save(roots, name: str, data: bytes) -> str:
    folder = data_file(roots, "intake")
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(data)
    return f"intake/{name}"


def import_file(sample, ctx, rel: str, **extra) -> dict:
    return invoke(sample.app, ctx, "library_import_file", {"file": rel, **extra})


# ---------------------------------------------------------------- reading

def test_word_reads_like_the_other_formats():
    html = resume_readers.docx_to_html(resume_docx())
    assert "<h2>Experience</h2>" in html and "<li>" in html     # Word headings and lists came through
    assert_reads_the_fixture(resume_import.parse(html, "html"))


def test_word_keeps_bold_as_the_words():
    html = resume_readers.docx_to_html(docx_from_lines("## Experience\n### Engineer | Contoso Health | 2019 – 2021\n"
                                                       "- Moved builds to **GitHub Actions**."))
    p = resume_import.parse(html, "html")
    assert p.roles[0].bullets == ["Moved builds to GitHub Actions."]


def test_pdf_reads_like_the_other_formats():
    assert_reads_the_fixture(resume_import.parse(resume_readers.pdf_to_text(resume_pdf()), "text"))


def test_pdf_list_items_are_made_whole_without_changing_words():
    text = "Experience\n\n•\n\n•\n\nCut release time from 3 hours to\n25 minutes.\nWrote the runbook.\n"
    assert resume_readers.pdf_text_to_lines(text) == ("Experience\n\n- Cut release time from 3 hours to 25 minutes.\n"
                                                       "- Wrote the runbook.\n")


def test_inline_bullet_marks_and_wrapped_lines_by_position():
    """The mark on the item's line ("•  item"); wrapped lines indented under the item's text, some in a
    box of their own; headings and the next role at the margin. Positions are in points."""
    L, M, T, R = 72.0, 82.0, 92.0, 540.0     # margin, bullet mark, item text, right edge
    boxes = [
        [(L, 300, "WORK EXPERIENCE")],
        [(L, 260, "Platform Engineer"),
         (L, R, "Contoso Health | Springfield, IL | Jan 2020 – Mar 2023"),        # long, but a header line
         (L, R - 5, "Contoso Health runs clinics across the region and builds its own scheduling"),
         (L, 200, "software for them.")],                                           # wrapped paragraph
        [(M, R - 3, "•  Cut release time from 3 hours to 25 minutes by moving 14 services to GitHub"),
         (T, 300, "Actions pipelines.")],                                           # wrapped, same box
        [(M, R - 2, "•  Wrote the on-call runbook, with steps for every alert we")],
        [(T, 250, "had seen in production")],                                       # wrapped, own box, no period
        [(L, 200, "Release Engineer")],                                             # the next role: margin
        [(L, 400, "Northwind Labs | 2017 – 2019")],
        [(M, 400, "•  Ran the nightly builds")],
    ]
    assert resume_readers.pdf_boxes_to_lines(boxes) == (
        "WORK EXPERIENCE\n\n"
        "Platform Engineer\nContoso Health | Springfield, IL | Jan 2020 – Mar 2023\n"
        "Contoso Health runs clinics across the region and builds its own scheduling software for them.\n\n"
        "- Cut release time from 3 hours to 25 minutes by moving 14 services to GitHub Actions pipelines.\n"
        "- Wrote the on-call runbook, with steps for every alert we had seen in production\n\n"
        "Release Engineer\n\nNorthwind Labs | 2017 – 2019\n\n"
        "- Ran the nightly builds\n")


def test_resume_builders_own_ats_pdf_imports_cleanly(sample, owner, roots):
    """A resume rendered by resume-builder's ATS template (inline marks, hanging indent) reads back
    with every bullet whole: nothing new to add, no role left out."""
    pytest.importorskip("weasyprint")
    from pathlib import Path
    out = invoke(sample.app, owner, "render", {"job_key": JOB, "variant": "ats"})
    rel = save(roots, "ats.pdf", Path(out["file"]).read_bytes())
    r = import_file(sample, owner, rel, dry_run=True)
    assert [a for a in r["added"] if a["kind"] in ("bullet", "role")] == [], r["added"]
    assert not [s for s in r["skipped"] if s.get("kind") == "role"], r["skipped"]


@pytest.mark.parametrize("head, name, kind", [
    (b"PK\x03\x04", "resume.docx", "docx"),
    (b"%PDF-1.7", "resume.pdf", "pdf"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "resume.doc", "doc"),
    (b"<html>", "resume.html", None),
    (b"# Alex", "resume.md", None),
])
def test_formats_are_told_apart_by_their_first_bytes(head, name, kind):
    assert resume_readers.sniff(head, name) == kind


# ---------------------------------------------------------------- importing

def test_import_a_word_resume(sample, owner, roots):
    rel = save(roots, "resume.docx", resume_docx())
    out = import_file(sample, owner, rel, dry_run=False)
    assert out["applied"] and out["format"] == "docx" and out["file"] == rel
    lib = sample.repo.load_library()
    added = {a["id"] for a in out["added"]}
    assert all(b.status == "draft" for b in lib.all_bullets().values() if b.id in added)
    assert "Moved 30 nightly batch jobs" in data_file(roots, "base.yaml").read_text(encoding="utf-8")
    assert (data_file(roots, rel)).read_bytes() == resume_docx()          # never changed


def test_import_a_pdf_resume(sample, owner, roots):
    rel = save(roots, "resume.pdf", resume_pdf())
    out = import_file(sample, owner, rel)
    assert out["format"] == "pdf" and out["added"]
    assert any("Read from a PDF" in a for a in out["attention"])


def test_the_preview_hash_holds_for_documents(sample, owner, roots):
    rel = save(roots, "resume.docx", resume_docx())
    preview = import_file(sample, owner, rel)
    done = import_file(sample, owner, rel, dry_run=False, expect_hash=preview["content_hash"])
    assert done["applied"]


# ---------------------------------------------------------------- refusals and limits

def refused(sample, owner, roots, name: str, data: bytes) -> str:
    rel = save(roots, name, data)
    with pytest.raises(ValidationFailed) as e:
        import_file(sample, owner, rel)
    return e.value.message


def test_old_word_files_are_refused_with_what_to_do(sample, owner, roots):
    msg = refused(sample, owner, roots, "resume.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)
    assert ".doc" in msg and "save it as .docx" in msg


def test_a_damaged_word_file_is_refused(sample, owner, roots):
    msg = refused(sample, owner, roots, "resume.docx", b"PK\x03\x04 not really a zip")
    assert "isn't a valid Word" in msg and ".docx, HTML or plain text" in msg


def test_a_zip_bomb_is_refused_before_conversion(sample, owner, roots, monkeypatch):
    monkeypatch.setattr(resume_readers, "MAX_UNZIPPED_BYTES", 1_000_000)
    bomb = docx_from_lines("## Experience", extra={"word/media/padding.bin": b"\0" * 2_000_000})
    assert "unpacks to more than" in refused(sample, owner, roots, "resume.docx", bomb)


def test_too_many_parts_are_refused(sample, owner, roots, monkeypatch):
    monkeypatch.setattr(resume_readers, "MAX_ZIP_ENTRIES", 10)
    many = docx_from_lines("## Experience", extra={f"word/media/{i}.bin": b"x" for i in range(20)})
    assert "too many parts" in refused(sample, owner, roots, "resume.docx", many)


def test_a_zip_that_isnt_word_is_refused(sample, owner, roots):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    assert "isn't a Word document" in refused(sample, owner, roots, "resume.docx", buf.getvalue())


def test_large_documents_are_refused(sample, owner, roots, monkeypatch):
    monkeypatch.setattr(resume_readers, "MAX_DOCUMENT_BYTES", 1_000)
    assert "larger than" in refused(sample, owner, roots, "resume.docx", resume_docx() + b"\0" * 2_000)


MINIMAL_PDF_NO_TEXT = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
                       b"trailer<</Root 1 0 R>>\n%%EOF\n")


def test_a_pdf_without_text_is_refused_as_a_scan(sample, owner, roots):
    msg = refused(sample, owner, roots, "scan.pdf", MINIMAL_PDF_NO_TEXT)
    assert "scanned image" in msg and ".docx, HTML or plain text" in msg


def test_a_slow_pdf_is_stopped(monkeypatch):
    with pytest.raises(resume_readers.ReadFailed) as e:
        resume_readers.pdf_to_text(MINIMAL_PDF_NO_TEXT, timeout=0.001)
    assert "longer than" in e.value.message


def test_a_damaged_pdf_is_refused(sample, owner, roots):
    assert "couldn't be read" in refused(sample, owner, roots, "resume.pdf", b"%PDF-1.4\nnot a real pdf at all")


# ---------------------------------------------------------------- the CLI

def test_cli_imports_a_word_resume(sample, roots, monkeypatch, capsys):
    save(roots, "resume.docx", resume_docx())
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["--user", USER, "import-resume", "intake/resume.docx", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "(docx)" in out and "Added" in out


def test_cli_refuses_to_pipe_a_document(sample, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("x"))
    assert main(["--user", USER, "import-resume", "--stdin", "--format", "pdf"]) == 1
    assert "can't be piped in" in capsys.readouterr().err
