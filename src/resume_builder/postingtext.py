"""Turn a captured posting into Markdown without changing its wording (PRD R13.3, R13.11).

`html_to_markdown` finds the page's main content and converts it mechanically: headings, lists,
paragraphs, emphasis, tables. Navigation, headers and footers outside the main content, scripts,
styles, forms and cookie or consent banners are dropped. Text is never reworded, summarized or
reordered; only HTML whitespace is collapsed, as a browser would.

`check_capture` refuses pages that hold too little posting text: an empty JavaScript shell, a login
page, or an error page. `posting_document` builds `posting.md` from a capture.

Pure functions, standard library only. Nothing here reads or writes files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

MIN_WORDS = 60  # fewer words of posting text than this is not a usable posting page

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
DROP = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "form", "button", "select",
        "textarea", "dialog", "head", "title", "picture", "video", "audio", "map"}
CHROME = {"nav", "header", "footer", "aside"}  # dropped unless inside the main content
DROP_ROLES = {"navigation", "banner", "contentinfo", "dialog", "alertdialog", "search", "complementary"}
# Matched against id, class and aria-label. ALWAYS_NOISE is dropped anywhere; CHROME_NOISE only outside
# the main content, where a "banner" or "share" class is page furniture rather than the posting.
ALWAYS_NOISE = re.compile(r"(^|[\s_-])(cookies?|consent|gdpr|onetrust|sr-only|visually-hidden|skip-link)([\s_-]|$)", re.I)
CHROME_NOISE = re.compile(r"(^|[\s_-])(banner|newsletter|share|social|breadcrumbs?|modal|popup|menu)([\s_-]|$)", re.I)
BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table",
         "tr", "blockquote", "pre", "dl", "dt", "dd", "hr", "br", "body", "html", "figure", "figcaption", "address",
         "header", "footer", "nav", "aside"}
BR = "\x00"  # marks a <br>; all other whitespace in HTML text collapses to one space, as in a browser
HARD_BREAK = "\\\n"  # a <br> in Markdown: a backslash line break, which editors don't strip like trailing spaces
WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)


class CaptureRefused(ValueError):
    """The captured text isn't a usable posting. `reason` is a short code."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


# ---------------------------------------------------------------- a small tolerant DOM

@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)  # Node or str
    parent: "Node | None" = None


class _Builder(HTMLParser):
    """Builds a forgiving tree: unknown end tags are ignored, unclosed tags close with their parent."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root
        self.json_ld: list[str] = []
        self.has_password = False
        self.noscript_text: list[str] = []
        self._raw: str | None = None  # inside <script type=application/ld+json>
        self._noscript = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "input" and a.get("type", "").lower() == "password":
            self.has_password = True
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._raw = ""
        if tag == "noscript":
            self._noscript += 1
        if tag == "p" and self.cur.tag == "p":  # <p> implicitly closes an open <p>
            self.cur = self.cur.parent
        if tag == "li" and self.cur.tag == "li":
            self.cur = self.cur.parent
        node = Node(tag, a, parent=self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.cur.tag == tag:
            self.cur = self.cur.parent

    def handle_endtag(self, tag):
        if tag == "script" and self._raw is not None:
            self.json_ld.append(self._raw)
            self._raw = None
        if tag == "noscript" and self._noscript:
            self._noscript -= 1
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if self._raw is not None:
            self._raw += data
            return
        data = data.replace(BR, "")  # a stray NUL in the page must not read as a line break
        if self._noscript:
            self.noscript_text.append(data)
        self.cur.children.append(data)


def _walk(node: Node):
    yield node
    for c in node.children:
        if isinstance(c, Node):
            yield from _walk(c)


def _text(node: Node) -> str:
    out = []
    for c in node.children:
        if isinstance(c, str):
            out.append(c)
        elif c.tag not in DROP:
            out.append(_text(c))
    return " ".join(out)


def _words(text: str) -> int:
    return len(WORD.findall(text))


def _is_noise(node: Node, inside_main: bool) -> bool:
    if node.tag in DROP:
        return True
    a = node.attrs
    if "hidden" in a or a.get("aria-hidden", "").lower() == "true" or re.search(r"display\s*:\s*none", a.get("style", ""), re.I):
        return True
    if a.get("role", "").lower() in DROP_ROLES and not inside_main:
        return True
    if node.tag in CHROME and not inside_main:
        return True
    ident = " ".join((a.get("id", ""), a.get("class", ""), a.get("aria-label", "")))
    if not ident.strip() or node.tag in ("main", "article", "body", "html"):
        return False
    return bool(ALWAYS_NOISE.search(ident)) or (not inside_main and bool(CHROME_NOISE.search(ident)))


def _main_content(root: Node) -> Node:
    """The page's main content: <main> or role=main, else the one <article> with the most text,
    else <body>."""
    nodes = list(_walk(root))
    for n in nodes:
        if n.tag == "main" or n.attrs.get("role", "").lower() == "main":
            return n
    articles = [n for n in nodes if n.tag == "article"]
    if articles:
        best = max(articles, key=lambda n: _words(_text(n)))
        if _words(_text(best)) >= MIN_WORDS:
            return best
    return next((n for n in nodes if n.tag == "body"), root)


# ---------------------------------------------------------------- Markdown rendering

class _Md:
    def __init__(self):
        self.blocks: list[str] = []
        self.inline: list[str] = []

    def flush(self):
        """End the current paragraph. <br> line breaks inside it become Markdown hard breaks."""
        _flush_lines(self)


def _escape_start(text: str) -> str:
    """Keep ordinary text from turning into Markdown structure (a heading, quote or list item)."""
    if re.match(r"(#{1,6}\s|>|[-+*]\s|\d+[.)]\s)", text):
        return "\\" + text
    return text


def _inline(node: Node, inside_main: bool) -> str:
    parts = []
    for c in node.children:
        if isinstance(c, str):
            parts.append(c)
            continue
        if _is_noise(c, inside_main):
            continue
        if c.tag == "br":
            parts.append(BR)
            continue
        inner = _inline(c, inside_main)
        if c.tag in ("strong", "b") and inner.strip():
            parts.append(_wrap(inner, "**"))
        elif c.tag in ("em", "i") and inner.strip():
            parts.append(_wrap(inner, "*"))
        elif c.tag == "code" and inner.strip():
            parts.append(f"`{inner.strip()}`")
        else:
            parts.append(inner)
    return "".join(parts)


def _wrap(text: str, mark: str) -> str:
    lead = " " if text[:1].isspace() else ""
    trail = " " if text[-1:].isspace() else ""
    return f"{lead}{mark}{text.strip()}{mark}{trail}"


def _clean_inline(text: str) -> str:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split(BR)]
    return "\n".join(ln for ln in lines if ln)


def _render(node: Node, md: _Md, inside_main: bool, heading_shift: int, list_depth: int = 0):
    for c in node.children:
        if isinstance(c, str):
            md.inline.append(c)
            continue
        if _is_noise(c, inside_main):
            continue
        t = c.tag
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            md.flush()
            level = min(6, int(t[1]) + heading_shift)
            text = _clean_inline(_inline(c, inside_main)).replace("\n", " ")
            if text:
                md.blocks.append("#" * level + " " + text)
        elif t in ("ul", "ol"):
            md.flush()
            items, n = [], 0
            for li in c.children:
                if isinstance(li, Node) and li.tag == "li" and not _is_noise(li, inside_main):
                    n += 1
                    items.append(_list_item(li, "1." if t == "ol" else "-", n, list_depth, inside_main, heading_shift))
                elif isinstance(li, Node) and li.tag in ("ul", "ol"):
                    sub = _Md()
                    _render(Node("#wrap", children=[li]), sub, inside_main, heading_shift, list_depth + 1)
                    items.extend(sub.blocks)
            if items:
                md.blocks.append("\n".join(items))
        elif t == "li":  # a stray <li> outside a list
            md.flush()
            md.blocks.append(_list_item(c, "-", 1, list_depth, inside_main, heading_shift))
        elif t == "table":
            md.flush()
            rows = []
            for tr in (n for n in _walk(c) if n.tag == "tr"):
                cells = [_clean_inline(_inline(td, inside_main)).replace("\n", " ") for td in tr.children
                         if isinstance(td, Node) and td.tag in ("td", "th")]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                md.blocks.append("\n".join(rows))
        elif t == "pre":
            md.flush()
            text = _text(c).strip("\n")
            if text.strip():
                md.blocks.append("```\n" + text + "\n```")
        elif t == "blockquote":
            md.flush()
            sub = _Md()
            _render(c, sub, inside_main, heading_shift, list_depth)
            sub.flush()
            if sub.blocks:
                md.blocks.append("\n".join("> " + ln if ln else ">" for ln in "\n\n".join(sub.blocks).split("\n")))
        elif t == "dt":
            md.flush()
            text = _clean_inline(_inline(c, inside_main)).replace("\n", " ")
            if text:
                md.blocks.append(f"**{text}**")
        elif t == "hr":
            md.flush()
        elif t == "br":
            md.inline.append(BR)
        elif t in BLOCK or t in ("main", "article", "section"):
            md.flush()
            _render(c, md, inside_main, heading_shift, list_depth)
            _flush_lines(md)
        else:
            md.inline.append(_inline(Node("#wrap", children=[c]), inside_main))
    _flush_lines(md)


def _flush_lines(md: _Md):
    """Flush inline text, keeping <br> line breaks as Markdown hard breaks."""
    raw = "".join(md.inline)
    md.inline = []
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.split(BR)]
    lines = [_escape_start(ln) for ln in lines if ln]
    if lines:
        md.blocks.append(HARD_BREAK.join(lines))


def _list_item(li: Node, marker: str, n: int, depth: int, inside_main: bool, heading_shift: int) -> str:
    indent = "  " * depth
    bullet = f"{n}." if marker == "1." else "-"
    sub = _Md()
    head = Node("#head", children=[x for x in li.children if not (isinstance(x, Node) and x.tag in ("ul", "ol"))])
    _render(head, sub, inside_main, heading_shift, depth)
    first = " ".join(b.replace(HARD_BREAK, " ") for b in sub.blocks).strip()
    out = [f"{indent}{bullet} {first}" if first else f"{indent}{bullet}"]
    for nested in (x for x in li.children if isinstance(x, Node) and x.tag in ("ul", "ol")):
        inner = _Md()
        _render(Node("#wrap", children=[nested]), inner, inside_main, heading_shift, depth + 1)
        out.extend(inner.blocks)
    return "\n".join(out)


# ---------------------------------------------------------------- public API

@dataclass(frozen=True)
class Converted:
    markdown: str
    words: int
    has_password: bool
    needs_javascript: bool
    json_ld_description: bool  # the text came from the page's JobPosting data, not its visible body


def html_to_markdown(html: str) -> Converted:
    b = _Builder()
    b.feed(html)
    b.close()
    main = _main_content(b.root)
    inside_main = main.tag in ("main", "article") or main.attrs.get("role", "").lower() == "main"
    md = _Md()
    _render(main, md, inside_main, heading_shift=1)
    text = _tidy("\n\n".join(md.blocks))
    noscript = " ".join(b.noscript_text).lower()
    needs_js = "javascript" in noscript or bool(re.search(r"enable javascript|requires javascript|javascript is (disabled|required)", text, re.I))
    from_ld = False
    if _words(text) < MIN_WORDS:
        # Many job sites render with JavaScript but embed the posting as schema.org JobPosting data.
        # Its description is the employer's own posting text, so it is used as published.
        ld = _job_posting_description(b.json_ld)
        if ld:
            inner = html_to_markdown(ld) if "<" in ld else Converted(_tidy(ld), _words(ld), False, False, False)
            title = _job_posting_title(b.json_ld)
            text = (f"## {title}\n\n" if title else "") + inner.markdown
            from_ld = True
    return Converted(text, _words(text), b.has_password, needs_js and not from_ld, from_ld)


def _job_postings(blocks: list[str]):
    for raw in blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                types = d.get("@type")
                if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
                    yield d
                stack.extend(v for k, v in d.items() if k == "@graph")


def job_postings(html: str) -> list[dict]:
    """The schema.org JobPosting objects embedded in a page as JSON-LD."""
    b = _Builder()
    b.feed(html)
    b.close()
    return list(_job_postings(b.json_ld))


def _job_posting_description(blocks: list[str]) -> str | None:
    for jp in _job_postings(blocks):
        desc = jp.get("description")
        if isinstance(desc, str) and desc.strip():
            return desc
    return None


def _job_posting_title(blocks: list[str]) -> str | None:
    for jp in _job_postings(blocks):
        t = jp.get("title")
        if isinstance(t, str) and t.strip():
            return re.sub(r"\s+", " ", t).strip()
    return None


def _plain_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.lower())


def visible_text(html: str) -> str:
    """The page's text with tags, scripts and styles removed (entities decoded), plus the text of any
    embedded JobPosting description."""
    import html as htmllib

    body = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", html, flags=re.S | re.I)
    parts = [htmllib.unescape(re.sub(r"<[^>]+>", " ", body))]
    for jp in job_postings(html):
        for key in ("title", "description"):
            if isinstance(jp.get(key), str):
                parts.append(htmllib.unescape(re.sub(r"<[^>]+>", " ", jp[key])))
    return "\n".join(parts)


def wording_preserved(markdown: str, raw: str, content_type: str) -> bool:
    """True when every word of `markdown` appears in the captured page, in the same order: nothing
    added, reworded or reordered (R13.11). List numbers are formatting and don't count."""
    md = re.sub(r"^\s*\d+[.)]\s", "", markdown, flags=re.M)
    source = visible_text(raw) if content_type == "text/html" else raw
    it = iter(_plain_words(source))
    return all(w in it for w in _plain_words(md))


def text_to_markdown(text: str) -> str:
    """Pasted or plain text is kept as is; only line endings and trailing spaces are normalized."""
    return _tidy(text)


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    return out + "\n" if out else ""


def convert(raw: str, content_type: str) -> str:
    """Markdown for a capture's raw content, refusing pages that aren't a usable posting (R13.3)."""
    if content_type == "text/plain":
        md = text_to_markdown(raw)
        if not md.strip():
            raise CaptureRefused("empty", "The text is empty.")
        return md
    c = html_to_markdown(raw)
    if c.words >= MIN_WORDS:
        return c.markdown
    if c.has_password:
        raise CaptureRefused("login-page", "The link opened a sign-in page, not the posting.")
    if c.needs_javascript:
        raise CaptureRefused("needs-javascript", "The page shows its content only with JavaScript, which intake doesn't run.")
    raise CaptureRefused("too-little-text", f"The page has too little text to be a job posting ({c.words} words).")


# ---------------------------------------------------------------- posting.md

NOTES_HEADING = "## My notes"
SEED_BODY_HINT = "(Paste the full job description here"
SOURCE_HEADING = "## Source"
NOTES_HINT = ("(Anything you noticed: team, tech stack, red flags. Your reasons for wanting the job go in "
              "`interest_notes` in application.yaml, because letters use only that for motivation.)")


def posting_document(markdown: str, source: str, captured_on: str, notes: str | None = None) -> str:
    """The text of posting.md: the posting as captured, where it came from, and the user's notes.
    `source` is the URL or a description such as "pasted text (intake/job.txt)"; `captured_on` is YYYY-MM-DD."""
    notes = (notes or "").strip() or NOTES_HINT
    return (f"# Job posting\n\n{markdown.strip()}\n\n{SOURCE_HEADING}\n\n{source}, captured {captured_on}.\n\n"
            f"{NOTES_HEADING}\n\n{notes}\n")


def notes_of(posting_text: str | None) -> str | None:
    """The user's "My notes" section from an existing posting.md, or None if it only has the hint."""
    if not posting_text or NOTES_HEADING not in posting_text:
        return None
    notes = posting_text.split(NOTES_HEADING, 1)[1].strip()
    return None if not notes or notes == NOTES_HINT else notes


def body_of(posting_text: str | None) -> str:
    """The posting part of posting.md: between "# Job posting" and "## Source" (or "## My notes")."""
    text = (posting_text or "").strip()
    if text.startswith("# Job posting"):
        text = text[len("# Job posting"):]
    for heading in (SOURCE_HEADING, NOTES_HEADING):
        if f"\n{heading}" in text:
            text = text.split(f"\n{heading}", 1)[0]
    text = text.strip()
    return "" if text.startswith(SEED_BODY_HINT) else text  # the seeded file's hint is not a posting
