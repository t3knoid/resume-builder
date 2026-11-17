"""Online help: the user guides, rendered for the tracker website (issue #34, PRD R8.11).

The guides are the Markdown files in `docs/` (one source). The Docker image copies the three user
guides into the package (`resume_builder/help/`), so help works offline and matches the running
version; a source checkout reads `docs/` directly. Contributor docs (PRD, ARCHITECTURE) are not help.

Rendering happens on the server with markdown-it-py: raw HTML in the Markdown is escaped, never passed
through; headings get the same anchors GitHub gives them, so links between the guides keep working;
links to the other guides become help pages; links to anything else in the repository go to GitHub,
marked as external.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

REPO_URL = "https://github.com/t3knoid/resume-builder"


@dataclass(frozen=True)
class Guide:
    slug: str
    title: str
    file: str


GUIDES = (
    Guide("quickstart", "Quick start", "QUICKSTART.md"),
    Guide("user-manual", "User manual", "USER-MANUAL.md"),
    Guide("ai-agents", "Working with an AI agent", "AI-AGENTS.md"),
)
BY_FILE = {g.file: g for g in GUIDES}
BY_SLUG = {g.slug: g for g in GUIDES}


def guides_dir() -> Path | None:
    """Where the guides are: packaged with the app (image, wheel), else the repository's docs/."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "help", here.parents[1] / "docs"):
        if all((candidate / g.file).is_file() for g in GUIDES):
            return candidate
    return None


_IMAGE_NAME = re.compile(r"^[a-z0-9-]+(?:/[a-z0-9-]+)*\.png$")


def help_image(name: str) -> Path | None:
    """A picture the guides show (`docs/images/...`, packaged as `help/images/...`), or None.

    Only lower-case PNG names inside the images folder, so the route can't reach any other file.
    """
    folder = guides_dir()
    if folder is None or not _IMAGE_NAME.match(name):
        return None
    path = folder / "images" / name
    return path if path.is_file() else None


def heading_slug(text: str, seen: dict[str, int]) -> str:
    """GitHub's anchor for a heading: lower case, punctuation removed, spaces to hyphens, and a
    -1, -2, ... suffix for repeats. Links written for GitHub therefore work in help too."""
    base = re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text.strip().lower()))
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


@dataclass
class Rendered:
    guide: Guide
    html: str
    toc: list[tuple[int, str, str]] = field(default_factory=list)   # (level, text, anchor)
    anchors: set[str] = field(default_factory=set)


def _markdown() -> MarkdownIt:
    # "commonmark" with html off: raw HTML is shown as text. Tables and strikethrough as on GitHub.
    return MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable(["table", "strikethrough"])


def _rewrite(href: str, source: str) -> tuple[str, bool]:
    """(new href, external?) for a link found in the guide `source` (a file name in docs/)."""
    if not href or href.startswith("#"):
        return href, False
    parts = urlsplit(href)
    if parts.scheme or href.startswith("//"):
        return href, True
    path, _, anchor = href.partition("#")
    name = path.split("/")[-1]
    if name in BY_FILE and (path == name or path in (f"./{name}", f"docs/{name}", f"../docs/{name}")):
        return f"/help/{BY_FILE[name].slug}" + (f"#{anchor}" if anchor else ""), False
    # Anything else in the repository (contributor docs, CHANGELOG, scripts): on GitHub.
    target = re.sub(r"^(\./)+", "", path)
    resolved = []
    for piece in ["docs", *target.split("/")]:
        if piece == "..":
            if resolved:
                resolved.pop()
        elif piece and piece != ".":
            resolved.append(piece)
    return f"{REPO_URL}/blob/main/{'/'.join(resolved)}" + (f"#{anchor}" if anchor else ""), True


def render_markdown(text: str, source: str = "") -> tuple[str, list[tuple[int, str, str]], set[str]]:
    md = _markdown()
    tokens = md.parse(text)
    seen: dict[str, int] = {}
    toc: list[tuple[int, str, str]] = []
    anchors: set[str] = set()
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            inline = tokens[i + 1]
            label = "".join(c.content for c in (inline.children or []) if c.type in ("text", "code_inline"))
            anchor = heading_slug(label, seen)
            tok.attrSet("id", anchor)
            anchors.add(anchor)
            toc.append((int(tok.tag[1]), label, anchor))
        elif tok.type == "inline":
            for child in tok.children or []:
                if child.type == "image":  # pictures next to the guides are served by the help route
                    src = str(child.attrGet("src") or "")
                    if src.startswith("images/"):
                        child.attrSet("src", "/help/" + src)
                if child.type == "link_open":
                    href, external = _rewrite(str(child.attrGet("href") or ""), source)
                    child.attrSet("href", href)
                    if external:
                        child.attrSet("target", "_blank")
                        child.attrSet("rel", "noopener noreferrer")
                        child.attrSet("class", "external")
    html = md.renderer.render(tokens, md.options, {})
    # A visible label on code blocks, so the macOS/Linux and Windows versions are easy to tell apart.
    html = re.sub(r'<pre><code class="language-(\w+)">',
                  lambda m: f'<div class="code-label">{CODE_LABELS.get(m.group(1), m.group(1))}</div>'
                            f'<pre><code class="language-{m.group(1)}">', html)
    return html, toc, anchors


CODE_LABELS = {"sh": "macOS, Linux", "bash": "macOS, Linux", "powershell": "Windows PowerShell", "json": "JSON",
               "toml": "TOML", "yaml": "YAML", "text": "text", "markdown": "Markdown"}


_cache: dict[str, tuple[float, Rendered]] = {}


def render_guide(slug: str) -> Rendered | None:
    guide = BY_SLUG.get(slug)
    folder = guides_dir()
    if guide is None or folder is None:
        return None
    path = folder / guide.file
    mtime = path.stat().st_mtime
    hit = _cache.get(slug)
    if hit and hit[0] == mtime:
        return hit[1]
    html, toc, anchors = render_markdown(path.read_text(encoding="utf-8"), guide.file)
    rendered = Rendered(guide, html, toc, anchors)
    _cache[slug] = (mtime, rendered)
    return rendered


@dataclass(frozen=True)
class Hit:
    guide: Guide
    heading: str
    anchor: str
    snippet: str
    in_heading: bool


def search(query: str, limit: int = 30) -> list[Hit]:
    """Sections whose heading or text contains every word of the query; heading matches first."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    folder = guides_dir()
    if not words or folder is None:
        return []
    hits: list[Hit] = []
    for guide in GUIDES:
        lines = (folder / guide.file).read_text(encoding="utf-8").splitlines()
        seen: dict[str, int] = {}
        sections: list[tuple[str, str, list[str]]] = []
        in_code = False
        for line in lines:
            if line.lstrip().startswith("```"):
                in_code = not in_code
            m = None if in_code else re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                title = re.sub(r"[`*_]", "", m.group(2)).strip()
                sections.append((title, heading_slug(title, seen), []))
            elif sections:
                sections[-1][2].append(line)
        for title, anchor, body in sections:
            text = " ".join(body)
            low_title, low_text = title.lower(), text.lower()
            if all(w in low_title or w in low_text for w in words):
                in_heading = all(w in low_title for w in words)
                snippet = ""
                for ln in body:
                    if any(w in ln.lower() for w in words) and ln.strip():
                        snippet = re.sub(r"[`*_|>#]", "", ln).strip()[:180]
                        break
                hits.append(Hit(guide, title, anchor, snippet, in_heading))
    hits.sort(key=lambda h: (not h.in_heading, GUIDES.index(h.guide)))
    return hits[:limit]
