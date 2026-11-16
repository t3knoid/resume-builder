"""Online help on the tracker website (issue #34, PRD R8.11).

The help is rendered from the user guides in docs/ (or their packaged copy in the image), so these
tests also keep the guides' internal links honest: a renamed heading that breaks a link fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resume_builder import __version__, help as helpdocs
from resume_builder.api import create_api

from .conftest import USER

REPO = Path(__file__).resolve().parent.parent
HREF = re.compile(r'<a ([^>]*?)href="([^"]*)"([^>]*)>')
IDS = re.compile(r'\bid="([^"]+)"')


@pytest.fixture
def client(sample):
    return TestClient(create_api(sample.app), base_url="http://localhost")


def guide_html(client, slug):
    r = client.get(f"/help/{slug}")
    assert r.status_code == 200, slug
    return r.text


# ---------------------------------------------------------------- pages

def test_help_home_lists_the_three_guides(client):
    html = client.get("/help").text
    for slug in ("quickstart", "user-manual", "ai-agents"):
        assert f'href="/help/{slug}"' in html
    assert f"v{__version__}" in html


def test_each_guide_renders_with_contents_tables_and_labelled_code(client):
    for g in helpdocs.GUIDES:
        html = guide_html(client, g.slug)
        assert "<h1" in html and "Contents" in html and f"v{__version__}" in html
    manual = guide_html(client, "user-manual")
    assert "<table>" in manual and "<pre><code" in manual
    agents = guide_html(client, "ai-agents")
    assert '<div class="code-label">Windows PowerShell</div><pre><code class="language-powershell">' in agents
    assert '<div class="code-label">macOS, Linux</div><pre><code class="language-sh">' in agents


def test_unknown_guide_is_404(client):
    assert client.get("/help/PRD").status_code == 404
    assert client.get("/help/architecture").status_code == 404


# ---------------------------------------------------------------- links

def test_every_internal_link_and_anchor_resolves(client):
    """Walk every link in the rendered help: other guides, sections, and nothing left pointing at a .md file."""
    pages = {g.slug: guide_html(client, g.slug) for g in helpdocs.GUIDES}
    ids = {slug: set(IDS.findall(html)) for slug, html in pages.items()}
    broken = []
    for slug, html in pages.items():
        for before, href, after in HREF.findall(html):
            attrs = before + after
            if href.startswith("#"):
                if href[1:] not in ids[slug]:
                    broken.append((slug, href))
            elif href == "/help":
                pass
            elif href.startswith("/help/"):
                target, _, anchor = href[len("/help/"):].partition("#")
                if target not in ids or (anchor and anchor not in ids[target]):
                    broken.append((slug, href))
            elif href.startswith("https://github.com/"):
                assert 'target="_blank"' in attrs and "noopener" in attrs and "external" in attrs, href
            elif href.startswith(("http://", "https://", "mailto:")):
                assert 'target="_blank"' in attrs, href
            else:
                broken.append((slug, href))
            assert not re.search(r"\.md(#|$)", href) or href.startswith("https://github.com/"), (slug, href)
    assert broken == []


def test_links_between_guides_are_rewritten():
    html, _, _ = helpdocs.render_markdown(
        "[a](AI-AGENTS.md#do-i-need-a-paid-ai-plan) [b](QUICKSTART.md) [c](PRD.md) [d](../CHANGELOG.md) [e](#here)\n\n# Here\n",
        "USER-MANUAL.md")
    assert 'href="/help/ai-agents#do-i-need-a-paid-ai-plan"' in html and 'href="/help/quickstart"' in html
    assert f'href="{helpdocs.REPO_URL}/blob/main/docs/PRD.md"' in html
    assert f'href="{helpdocs.REPO_URL}/blob/main/CHANGELOG.md"' in html
    assert 'href="#here"' in html and 'id="here"' in html


def test_heading_anchors_match_github():
    seen: dict[str, int] = {}
    assert helpdocs.heading_slug("2. Setting up", seen) == "2-setting-up"
    assert helpdocs.heading_slug("Do I need a paid AI plan?", seen) == "do-i-need-a-paid-ai-plan"
    assert helpdocs.heading_slug("When something's wrong", seen) == "when-somethings-wrong"
    assert helpdocs.heading_slug("Settings (.env)", seen) == "settings-env"
    assert helpdocs.heading_slug("Settings (.env)", seen) == "settings-env-1"


# ---------------------------------------------------------------- safety

def test_raw_html_in_a_guide_is_escaped():
    html, _, _ = helpdocs.render_markdown('<script>alert(1)</script>\n\nText <img src=x onerror=alert(2)> [x](javascript:alert(3))\n')
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img" not in html and 'href="javascript:' not in html


def test_help_pages_carry_the_security_headers_and_the_host_check(client):
    r = client.get("/help/user-manual")
    csp = r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    script = csp.split("script-src", 1)[1].split(";", 1)[0].strip()
    assert script == "'self'"                       # only the site's own script files
    assert "unsafe-eval" not in csp and "unsafe-inline" not in script
    assert client.get("/help", headers={"host": "evil.example"}).status_code == 400


# ---------------------------------------------------------------- the help window

def test_every_page_has_a_help_link_that_opens_the_help_window(client, sample):
    for path in ("/", f"/u/{USER}/", f"/u/{USER}/applications", f"/u/{USER}/proposals", f"/u/{USER}/login"):
        html = client.get(path).text
        assert '<a href="/help" target="rb-help" data-help>Help</a>' in html, path
        assert '<script src="/static/help.js" defer></script>' in html, path


def test_contextual_help_links_point_to_existing_sections(client):
    pages = {g.slug: set(IDS.findall(guide_html(client, g.slug))) for g in helpdocs.GUIDES}
    found = []
    for path in (f"/u/{USER}/", f"/u/{USER}/proposals", f"/u/{USER}/login"):
        for before, href, after in HREF.findall(client.get(path).text):
            if "help-link" not in before + after:
                continue
            target, _, anchor = href[len("/help/"):].partition("#")
            assert target in pages and anchor in pages[target], (path, href)
            assert 'target="rb-help"' in before + after and "data-help" in before + after
            found.append(href)
    assert len(found) == 4


def test_the_script_keeps_one_help_window():
    r_js = (REPO / "src" / "resume_builder" / "web" / "static" / "help.js").read_text(encoding="utf-8")
    assert 'var NAME = "rb-help";' in r_js and r_js.count("window.open(") == 1
    assert "window.open(link.href, NAME, FEATURES)" in r_js and "win.focus()" in r_js
    assert "eval" not in r_js and "innerHTML" not in r_js


def test_the_script_is_served(client):
    r = client.get("/static/help.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "rb-help" in r.text


# ---------------------------------------------------------------- search

def test_search_finds_sections_headings_first(client):
    hits = helpdocs.search("paid plan")
    assert hits and hits[0].heading == "Do I need a paid AI plan?" and hits[0].guide.slug == "ai-agents"
    html = client.get("/help/search", params={"q": "paid plan"}).text
    assert 'href="/help/ai-agents#do-i-need-a-paid-ai-plan"' in html
    assert helpdocs.search("") == [] and helpdocs.search("x") == []


def test_search_query_is_escaped(client):
    html = client.get("/help/search", params={"q": "<script>alert(1)</script>"}).text
    assert "<script>alert(1)" not in html


# ---------------------------------------------------------------- packaging

def test_only_the_user_guides_are_packaged():
    if (REPO / "Dockerfile").is_file():   # in a source checkout
        dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
        copies = [ln for ln in dockerfile.splitlines() if ln.startswith("COPY docs/")]
        assert copies == ["COPY docs/QUICKSTART.md docs/USER-MANUAL.md docs/AI-AGENTS.md ./src/resume_builder/help/"]
    packaged = Path(helpdocs.__file__).resolve().parent / "help"
    if packaged.is_dir():   # in the image
        assert sorted(p.name for p in packaged.iterdir()) == sorted(g.file for g in helpdocs.GUIDES)


def test_web_files_have_no_control_characters():
    """A mangled escape once put a control character into the link-arrow CSS, showing " 97"."""
    web = Path(helpdocs.__file__).parent / "web"
    bad = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
    for f in [*web.rglob("*.html"), *web.rglob("*.js"), *web.rglob("*.css")]:
        assert not bad.search(f.read_text(encoding="utf-8")), f
    assert r"\2197" in (web / "templates" / "base.html").read_text(encoding="utf-8")
