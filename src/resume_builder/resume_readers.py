"""Word and PDF resumes as text the import parser reads (issue #39, PRD R2.12).

Word (.docx): `mammoth` turns the document into HTML, keeping headings (Heading 1–6 styles), Word's
lists (bullets), bold and tables in reading order; the import's own HTML reader takes it from there.
Images are dropped. PDF: `pdfminer.six` extracts the text layer, in a separate process with a time
limit, since a PDF is untrusted input; there is no OCR, so a scan is refused. Both run only on bytes
the sync adapter read from the user's intake folder, never on a path or a link, and never reach the
network. These are the converters MarkItDown uses inside, without its file-type detector and its
~200 MB of dependencies (D39).
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
import zipfile

MAX_DOCUMENT_BYTES = 10_000_000     # a Word or PDF resume larger than this is refused
MAX_ZIP_ENTRIES = 1_000             # a .docx is a zip: more entries than this is not a resume (zip bombs)
MAX_UNZIPPED_BYTES = 50_000_000     # nor is a .docx that unpacks to more than this
MAX_PDF_PAGES = 20
PDF_TIMEOUT_SECONDS = 30
MIN_PDF_WORDS = 30                  # fewer words than this: no text layer (a scan)

DOCX = "docx"
PDF = "pdf"
DOC = "doc"   # the old binary Word format: not read


class ReadFailed(Exception):
    """A document that can't be read. The message says why, in plain words."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def sniff(head: bytes, name: str | None = None) -> str | None:
    """docx, pdf or doc from the first bytes (and the file name), or None for a text format."""
    low = (name or "").lower()
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):   # OLE2: .doc (and other old Office files)
        return DOC
    if head.startswith(b"PK\x03\x04") and (low.endswith(".docx") or not low.endswith((".html", ".htm", ".md", ".txt"))):
        return DOCX
    if low.endswith(".docx"):
        return DOCX
    if low.endswith(".pdf"):
        return PDF
    if low.endswith(".doc"):
        return DOC
    return None


def check_size(data: bytes) -> None:
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ReadFailed(f"it's larger than {MAX_DOCUMENT_BYTES // 1_000_000} MB")


# ---------------------------------------------------------------- Word

def _check_zip(data: bytes) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ReadFailed("it isn't a valid Word (.docx) file") from None
    infos = z.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ReadFailed("it holds too many parts to be a resume")
    if sum(i.file_size for i in infos) > MAX_UNZIPPED_BYTES:
        raise ReadFailed(f"it unpacks to more than {MAX_UNZIPPED_BYTES // 1_000_000} MB")
    if "word/document.xml" not in {i.filename for i in infos}:
        raise ReadFailed("it isn't a Word document (no word/document.xml inside)")


def docx_to_html(data: bytes) -> str:
    """The document as HTML: headings, lists, bold and tables kept, images dropped."""
    import mammoth

    check_size(data)
    _check_zip(data)
    try:
        result = mammoth.convert_to_html(io.BytesIO(data), convert_image=mammoth.images.img_element(lambda image: {}))
    except Exception as exc:   # a damaged document: report it, never crash the import
        raise ReadFailed(f"the Word file couldn't be read ({type(exc).__name__})") from None
    html = re.sub(r"<img\b[^>]*>", "", result.value)
    return f"<html><body>{html}</body></html>"


# ---------------------------------------------------------------- PDF

# Runs in a child process: a malformed PDF can make a parser slow, and a thread can't be stopped.
# It imports only pdfminer, reads the PDF's bytes from stdin and writes UTF-8 text to stdout.
_PDF_CHILD = """
import io, sys
data = sys.stdin.buffer.read()
try:
    from pdfminer.high_level import extract_text
    from pdfminer.pdfdocument import PDFPasswordIncorrect, PDFEncryptionError
except Exception:
    sys.exit(5)
try:
    text = extract_text(io.BytesIO(data), maxpages=int(sys.argv[1]))
except (PDFPasswordIncorrect, PDFEncryptionError):
    sys.exit(3)
except Exception:
    sys.exit(4)
sys.stdout.buffer.write(text.encode("utf-8"))
"""


def pdf_to_text(data: bytes, timeout: float = PDF_TIMEOUT_SECONDS, pages: int = MAX_PDF_PAGES) -> str:
    """The PDF's text layer, page by page. Refuses scans (no text), password-protected files and
    files that take too long."""
    check_size(data)
    if not data.startswith(b"%PDF-"):
        raise ReadFailed("it isn't a PDF file")
    try:
        proc = subprocess.run([sys.executable, "-I", "-c", _PDF_CHILD, str(pages)], input=data,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ReadFailed(f"reading it took longer than {timeout:g} seconds") from None
    if proc.returncode == 3:
        raise ReadFailed("it's protected with a password")
    if proc.returncode == 5:
        raise ReadFailed("PDF reading isn't available in this installation")
    if proc.returncode != 0:
        raise ReadFailed("the PDF couldn't be read (it may be damaged)")
    text = proc.stdout.decode("utf-8", "replace").replace("\f", "\n\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    if len(re.findall(r"[^\W\d_]{2,}", text)) < MIN_PDF_WORDS:
        raise ReadFailed("it has no text to read, so it may be a scanned image (text recognition isn't supported)")
    return pdf_text_to_lines(text)


_MARK = re.compile(r"[•●▪◦‣·∙■□➢➤►\-–*]")
_ENDS_SENTENCE = re.compile(r"[.!?:;)\]]$")


def _join(lines: list[str]) -> str:
    """Wrapped lines of one item back into one line. A line ending in a hyphen joins without a space
    (the hyphen is kept: "alex-" + "morgan" is "alex-morgan"); otherwise with one space."""
    out = ""
    for ln in lines:
        out = out + ln if out.endswith("-") else (f"{out} {ln}" if out else ln)
    return out


def _items(lines: list[str], n: int) -> list[str]:
    """`lines` hold `n` list items, wrapped. One line each when the counts match; otherwise a new item
    starts after a line that ends a sentence and before one that starts with a capital or digit."""
    if n <= 1:
        return [_join(lines)]
    if len(lines) == n:
        return lines
    cuts = [i for i in range(1, len(lines)) if _ENDS_SENTENCE.search(lines[i - 1]) and re.match(r"[A-Z0-9]", lines[i])]
    cuts = cuts[: n - 1]
    bounds = [0, *cuts, len(lines)]
    return [_join(lines[a:b]) for a, b in zip(bounds, bounds[1:]) if a < b]


def pdf_text_to_lines(text: str) -> str:
    """pdfminer's text, with list items made whole again. pdfminer puts a list's bullet marks in a text
    box of their own, before the items, and keeps line wrapping; the import parser needs "- item" lines.
    Only line breaks and bullet marks change: every word stays as extracted, in order."""
    out: list[str] = []
    pending = 0
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if all(_MARK.fullmatch(ln) for ln in lines):
            pending += len(lines)
            continue
        if pending:
            out.append("\n".join(f"- {item}" for item in _items(lines, pending)))
            pending = 0
        else:
            joined: list[str] = []
            for ln in lines:   # a word broken at a hyphen (a long link, a hyphenated name) is one word again
                if joined and joined[-1].endswith("-") and not joined[-1].endswith(" -"):
                    joined[-1] += ln
                else:
                    joined.append(ln)
            out.append("\n".join(joined))
    return "\n\n".join(out) + "\n"
