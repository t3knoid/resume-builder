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
import json
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
# It imports only pdfminer, reads the PDF's bytes from stdin and writes JSON to stdout: per page, the
# text boxes in pdfminer's reading order, each a list of its lines as [left edge, right edge, text].
# The edges tell a wrapped line (indented under its bullet's text) from a new heading at the margin.
_PDF_CHILD = """
import io, json, sys
data = sys.stdin.buffer.read()
try:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer, LTTextLine
    from pdfminer.pdfdocument import PDFPasswordIncorrect, PDFEncryptionError
except Exception:
    sys.exit(5)
def lines(box):
    if isinstance(box, LTTextLine):
        return [[round(box.x0, 1), round(box.x1, 1), box.get_text()]]
    return [ln for child in box if isinstance(child, LTTextContainer) for ln in lines(child)]
try:
    pages = [[lines(el) for el in page if isinstance(el, LTTextContainer)]
             for page in extract_pages(io.BytesIO(data), maxpages=int(sys.argv[1]))]
except (PDFPasswordIncorrect, PDFEncryptionError):
    sys.exit(3)
except Exception:
    sys.exit(4)
sys.stdout.buffer.write(json.dumps(pages).encode("utf-8"))
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
    try:
        pages = json.loads(proc.stdout.decode("utf-8", "replace"))
        boxes = [[(float(x0), float(x1), _clean(t)) for x0, x1, t in box] for page in pages for box in page]
    except (ValueError, TypeError):
        raise ReadFailed("the PDF couldn't be read (it may be damaged)") from None
    words = sum(len(re.findall(r"[^\W\d_]{2,}", t)) for box in boxes for _, _, t in box)
    if words < MIN_PDF_WORDS:
        raise ReadFailed("it has no text to read, so it may be a scanned image (text recognition isn't supported)")
    return pdf_boxes_to_lines(boxes)


def _clean(text: str) -> str:
    return text.replace("\x00", "").replace(" ", " ").strip()


_MARK = re.compile(r"[•●▪◦‣·∙■□➢➤►\-–*]")
_INLINE_MARK = re.compile(r"[•●▪◦‣·∙■□➢➤►\-–*]\s+(?=\S)")   # "•  item" on one line
_ENDS_SENTENCE = re.compile(r"[.!?:;)\]]$")
_HEADER_LINE = re.compile(r"\s\|\s|(?:(?:19|20)\d\d|present|current|now)\)?$", re.I)   # "a | b", "... – 2024": a full line
_MAX_INDENT = 40.0   # points: a wrapped line sits under its item's text, a little right of the mark
_WRAP_ZONE = 0.15     # a line ending this close to the right margin (share of the text width) was wrapped


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
    """Like `pdf_boxes_to_lines`, from plain text with blank lines between text boxes and no positions."""
    boxes = [[(None, None, _clean(ln)) for ln in block.split("\n") if _clean(ln)] for block in re.split(r"\n\s*\n", text)]
    return pdf_boxes_to_lines(boxes)


Line = tuple  # (left edge or None, right edge or None, text)


def pdf_boxes_to_lines(boxes: list[list[Line]]) -> str:
    """pdfminer's text boxes as lines the import parser reads, with list items ("- item") and wrapped
    paragraphs made whole again. Only line breaks and bullet marks change: every word stays as
    extracted, in order.

    Bullets come two ways. Some PDFs put the marks in a box of their own, before the items. Others put
    the mark on the item's first line ("•  Automated ..."); its wrapped lines follow, sometimes in a box
    of their own, indented under the item's text. Positions decide what continues an item: a line
    indented right of the mark continues it, a line at the margin (a heading, the next role) doesn't.
    A plain line that reached the right margin without ending a sentence was wrapped, so the next line
    of its box continues it. Without positions, only a lowercase start continues an unfinished item."""
    edges = [(x0, x1) for box in boxes for x0, x1, _ in box if x0 is not None]
    left = min((a for a, _ in edges), default=None)
    right = max((b for _, b in edges), default=None)

    def wrapped(end, text) -> bool:
        return (right is not None and end is not None and not _ENDS_SENTENCE.search(text)
                and not _HEADER_LINE.search(text) and end >= right - _WRAP_ZONE * (right - left))

    def continues(item: dict, x0, text: str) -> bool:
        if _MARK.fullmatch(text) or _INLINE_MARK.match(text):
            return False
        if x0 is None or item["x"] is None:
            return text[:1].islower() and not _ENDS_SENTENCE.search(item["text"])
        if item["indent"] is not None:
            return abs(x0 - item["indent"]) <= 2
        return item["x"] + 1 < x0 <= item["x"] + _MAX_INDENT or (x0 <= item["x"] + 1 and wrapped(item["end"], item["text"]))

    entries: list[dict] = []     # {"kind": "item" | "line" | "break", ...}
    pending = 0                  # bullet marks seen in boxes of their own, waiting for their items
    item: dict | None = None     # the list item that a following line may continue
    for box in boxes:
        lines = [ln for ln in box if ln[2]]
        if not lines:
            continue
        if all(_MARK.fullmatch(t) for _, _, t in lines):
            pending += len(lines)
            item = None
            continue
        entries.append({"kind": "break"})
        if pending:
            entries += [{"kind": "item", "text": t} for t in _items([t for _, _, t in lines], pending)]
            pending, item = 0, None
            continue
        prev: dict | None = None  # the plain line before, in this box
        for x0, x1, t in lines:
            m = _INLINE_MARK.match(t)
            if m:
                item = {"kind": "item", "text": t[m.end():], "x": x0, "end": x1, "indent": None}
                entries.append(item)
                prev = None
            elif item is not None and continues(item, x0, t):
                item["text"] = _join([item["text"], t])
                item["end"] = x1
                if x0 is not None and x0 > (item["x"] or 0) + 1:
                    item["indent"] = x0
            elif prev is not None and (prev["text"].endswith("-") and not prev["text"].endswith(" -")
                                       or wrapped(prev["end"], prev["text"])):
                prev["text"] = _join([prev["text"], t])   # a broken word or a wrapped paragraph line
                prev["end"] = x1
            else:
                item = None
                prev = {"kind": "line", "text": t, "end": x1}
                entries.append(prev)

    out: list[str] = []
    for e in entries:
        if e["kind"] == "break":
            if out and out[-1]:
                out.append("")
        elif e["kind"] == "item":
            if len(out) >= 2 and not out[-1] and out[-2].startswith("- "):
                out.pop()        # items of one list stay together
            out.append(f"- {e['text']}")
        else:
            out.append(e["text"])
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"
