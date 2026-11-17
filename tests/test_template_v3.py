"""Resume template v3: v2 with a compact header (contact details and links on one line)."""

import re

from .test_template_v2 import _html


def _header(html: str) -> str:
    return html[html.index('<header class="contact">'):html.index("</header>")]


def test_v3_is_the_newest_and_puts_contact_details_on_one_line(app, sample, owner):
    header = _header(_html(app, sample, owner))
    line = re.search(r'<address class="contact-info">(.*?)</address>', header).group(1)
    assert '<span class="email">alex@example.com</span>' in line
    assert '<a href="https://github.com/example-alex">github.com/example-alex</a>' in line
    assert " | " in line and "<div" not in header.replace('<div class="title">', "")


def test_v3_ats_keeps_labels_and_full_urls_on_one_line(app, sample, owner):
    header = _header(_html(app, sample, owner, variant="ats"))
    line = re.search(r'<p class="contact-line">(.*?)</p>', header).group(1)
    assert "Email: alex@example.com | " in line
    assert '<a href="https://github.com/example-alex">https://github.com/example-alex</a>' in line
    assert "<br>" not in header


def test_v2_keeps_one_line_per_item(app, sample, owner):
    header = _header(_html(app, sample, owner, template_version=2))
    assert '<div class="email">alex@example.com</div>' in header
    assert "Email: alex@example.com<br>" in _header(_html(app, sample, owner, variant="ats", template_version=2))
