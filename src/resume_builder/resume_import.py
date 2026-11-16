"""Read an existing resume (HTML, Markdown or plain text) into structured entries (issue #1).

Plain parsing, no AI: sections are found by their headings, entries by their header lines and
bullets. Every piece of text is the resume's own wording; nothing is reworded, completed or
estimated. Where the parser has to choose (which part of a header is the title and which the
company), it says so in `notes`, so the user checks it. Turning entries into draft library items,
ids and duplicate checks is the service's job (`services/library_import.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as _f

# ---------------------------------------------------------------- sections

SECTION_NAMES = {
    "summary": ("summary", "professional summary", "career summary", "profile", "professional profile", "about",
                "about me", "objective", "career objective", "overview", "introduction"),
    "experience": ("experience", "work experience", "professional experience", "relevant experience", "employment",
                   "employment history", "work history", "career history", "career", "positions"),
    "projects": ("projects", "personal projects", "side projects", "selected projects", "key projects", "open source",
                 "volunteer", "volunteering", "volunteer experience", "volunteer work"),
    "education": ("education", "academic background", "education and training", "academics"),
    "certifications": ("certifications", "certification", "certificates", "licenses", "licenses and certifications",
                       "certifications and licenses", "licences", "credentials"),
    "skills": ("skills", "technical skills", "core skills", "key skills", "skills and tools", "tools", "technologies",
               "tools and technologies", "competencies", "core competencies", "expertise", "technical expertise"),
}
# Recognized but not imported: reported, so the user knows they were left out.
OTHER_SECTIONS = ("awards", "honors", "honours", "publications", "languages", "interests", "hobbies", "references",
                  "activities", "patents", "talks", "presentations", "memberships", "affiliations", "courses",
                  "training", "contact", "contact information", "links", "achievements")
_SECTION_OF = {name: kind for kind, names in SECTION_NAMES.items() for name in names}

MONTHS = {m: i for i, names in enumerate((("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
                                          ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
                                          ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
                                          ("dec", "december")), start=1) for m in names}
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = rf"(?:{_MONTH}\.?,?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}}[-/.]\d{{1,2}}(?!\d)|(?<!\d)(?:19|20)\d{{2}}(?!\d))"
_NOW = r"(?:present|current|currently|now|today|ongoing)"
DATE_RANGE = re.compile(rf"\(?\s*(?P<start>{_DATE})\s*(?:(?:-|–|—|to|until|through|thru)\s*(?P<end>{_DATE}|{_NOW}))?\s*\)?", re.I)

BULLET = re.compile(r"^\s*(?:[-*+•·▪●◦‣–]|\d+[.)])\s+")
URL = re.compile(r"(?:https?://|www\.)[^\s)>\]]+|\b(?:linkedin\.com|github\.com|gitlab\.com)/[^\s)>\]]+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d ().-]{7,}\d)(?!\d)")

TITLE_WORDS = re.compile(
    r"\b(engineer|developer|programmer|manager|director|analyst|administrator|admin|specialist|consultant|lead|"
    r"architect|intern|scientist|designer|officer|coordinator|technician|head|vp|vice president|president|associate|"
    r"assistant|founder|co-founder|owner|operator|support|sre|devops|tester|writer|editor|teacher|instructor|"
    r"professor|researcher|nurse|accountant|advisor|adviser|representative|supervisor|principal|staff|chief|cto|ceo|"
    r"cfo|cio|executive|partner|contractor|freelancer|volunteer)s?\b", re.I)
COMPANY_WORDS = re.compile(
    r"\b(inc|llc|ltd|limited|corp|corporation|co|company|group|labs?|laboratories|technologies|technology|systems|"
    r"solutions|software|services|bank|university|college|hospital|health|agency|partners|holdings|gmbh|plc|ag|sa|"
    r"studios?|networks?|consulting|ventures|foundation|institute|department|government|county|city of)\b\.?", re.I)
LOCATION = re.compile(r"^(?:(?i:remote|hybrid|on-?site)|[A-Z][A-Za-z .'-]+,\s*(?:[A-Z]{2}|[A-Z][a-z]+(?: [A-Z][a-z]+)*)"
                      r"(?:\s*\((?:remote|hybrid)\))?)$")
def is_location(part: str) -> bool:
    """"Austin, TX", "Remote": not a company or a title that happens to have a comma."""
    return bool(LOCATION.match(part)) and not TITLE_WORDS.search(part) and not COMPANY_WORDS.search(part)


SCHOOL_WORDS = re.compile(r"\b(university|universit[éy]|universidad|college|institute|school|academy|polytechnic|"
                          r"conservatory|seminary)\b", re.I)
DEGREE = re.compile(
    r"\b(b\.?\s?s\.?c?\.?|b\.?\s?a\.?|b\.?\s?eng\.?|b\.?\s?tech\.?|bachelor(?:'s|s)?(?:\s+of\s+[a-z]+(?:\s+[a-z]+)?)?|"
    r"m\.?\s?s\.?c?\.?|m\.?\s?a\.?|m\.?\s?eng\.?|m\.?b\.?a\.?|master(?:'s|s)?(?:\s+of\s+[a-z]+(?:\s+[a-z]+)?)?|"
    r"ph\.?\s?d\.?|doctor(?:ate)?(?:\s+of\s+[a-z]+)?|associate(?:'s|s)?(?:\s+of\s+[a-z]+(?:\s+[a-z]+)?)?|"
    r"a\.?\s?s\.?|a\.?\s?a\.?|diploma|high school diploma|ged)(?=\W|$)", re.I)


# ---------------------------------------------------------------- results

@dataclass
class Entry:
    """One role or project as written: header parts, dates and bullet texts."""

    header: str
    title: str | None = None
    company: str | None = None
    name: str | None = None              # projects
    location: str | None = None
    description: str | None = None       # projects: what followed the name on the header line
    start: str | None = None
    end: str | None = None
    bullets: list[str] = _f(default_factory=list)
    notes: list[str] = _f(default_factory=list)


@dataclass
class EducationEntry:
    text: str
    institution: str | None = None
    degree: str | None = None
    field: str | None = None
    start: str | None = None
    end: str | None = None
    notes: list[str] = _f(default_factory=list)


@dataclass
class CertificationEntry:
    text: str
    name: str | None = None
    issuer: str | None = None
    date: str | None = None


@dataclass
class SkillLine:
    name: str | None
    items: list[str]


@dataclass
class ParsedResume:
    summary: list[str] = _f(default_factory=list)
    roles: list[Entry] = _f(default_factory=list)
    projects: list[Entry] = _f(default_factory=list)
    education: list[EducationEntry] = _f(default_factory=list)
    certifications: list[CertificationEntry] = _f(default_factory=list)
    skills: list[SkillLine] = _f(default_factory=list)
    links: list[str] = _f(default_factory=list)
    contact_dropped: bool = False        # an email address or phone number was found and left out
    skipped_sections: list[str] = _f(default_factory=list)
    unplaced: list[str] = _f(default_factory=list)   # text the parser couldn't attach to anything


# ---------------------------------------------------------------- text normalizing

def detect_format(text: str, name: str | None = None) -> str:
    low = (name or "").lower()
    if low.endswith((".html", ".htm")):
        return "html"
    if low.endswith((".md", ".markdown")):
        return "markdown"
    if low.endswith(".txt"):
        return "text"
    head = text[:2000].lower()
    if "<html" in head or "<body" in head or len(re.findall(r"<(?:p|div|li|h[1-6]|ul|section|article)\b", head)) >= 3:
        return "html"
    if re.search(r"^#{1,6}\s", text, re.M):
        return "markdown"
    return "text"


def html_to_lines(html: str) -> str:
    """The whole resume page as Markdown. Unlike a job posting, every part of the page is content:
    a resume puts roles in <header> and <aside> elements too, so nothing but hidden and script-like
    elements is dropped."""
    from .postingtext import HARD_BREAK, _Builder, _Md, _render, _tidy, _walk

    b = _Builder()
    b.feed(html)
    b.close()
    body = next((n for n in _walk(b.root) if n.tag == "body"), b.root)
    md = _Md()
    _render(body, md, inside_main=True, heading_shift=0)
    return _tidy("\n\n".join(md.blocks)).replace(HARD_BREAK, "\n")


def _clean(text: str) -> str:
    """Markdown emphasis, escapes and links reduced to their words."""
    text = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1 \2", text)           # [label](url) -> label url
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>|~])", r"\1", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*])", r"\2", text)
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _section_kind(line: str) -> tuple[str | None, str] | None:
    """(kind, heading) if `line` is a section heading: a Markdown heading, or a short line that is a
    known section name (any case, optional colon). kind None: a section that isn't imported."""
    m = re.match(r"^\s*#{1,6}\s+(.*)$", line)
    raw = _clean(m.group(1) if m else line)
    name = re.sub(r"[:\s]+$", "", raw).strip().lower()
    name = re.sub(r"\s*&\s*", " and ", name)
    if name in _SECTION_OF:
        return _SECTION_OF[name], raw.rstrip(": ")
    if name in OTHER_SECTIONS:
        return None, raw.rstrip(": ")
    if m and len(name.split()) <= 4 and not DATE_RANGE.search(name):
        # Any other top-level Markdown heading names a section we don't import, unless it sits inside
        # experience or projects, where headings are entry headers (decided by the caller).
        return "unknown", raw.rstrip(": ")
    if not m and _CAPS_HEADING.fullmatch(raw) and not BULLET.match(line) and not DATE_RANGE.search(raw):
        # In plain text (a PDF), a short line in capitals is a heading too ("HOME LAB & PORTFOLIO").
        # The caller ignores it at the top (the name) and in sections where capitals may name a
        # company, school or issuer; after a summary or skills it ends that section.
        return "unknown_caps", raw.rstrip(": ")
    return None


# Two to six words in capitals (spaces, & / ' - allowed, no digits), at least four letters.
_CAPS_HEADING = re.compile(r"(?=(?:.*[A-Z]){4})[A-Z][A-Z&/'’-]*(?: [A-Z&/'’-]+){1,5}:?")


# ---------------------------------------------------------------- dates

def _one_date(token: str) -> str | None:
    t = token.strip().lower().rstrip(".,")
    if re.fullmatch(_NOW, t, re.I):
        return "present"
    m = re.fullmatch(rf"({_MONTH})\.?,?\s+(\d{{4}})", t, re.I)
    if m:
        month = MONTHS.get(m.group(1)[:4].rstrip(".")) or MONTHS.get(m.group(1)[:3])
        return f"{m.group(2)}-{month:02d}" if month else m.group(2)
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    if re.fullmatch(r"(?:19|20)\d{2}", t):
        return t
    return None


def take_dates(text: str) -> tuple[str | None, str | None, str]:
    """(start, end, text without the dates). A single date gives start and end the same value;
    `present` (or current, now) as the end gives end `present`."""
    best = None
    for m in DATE_RANGE.finditer(text):
        if best is None or (m.group("end") and not best.group("end")):
            best = m
    if best is None:
        return None, None, text
    start = _one_date(best.group("start"))
    end = _one_date(best.group("end")) if best.group("end") else start
    rest = (text[:best.start()] + " " + text[best.end():]).strip()
    rest = re.sub(r"\(\s*\)", "", rest)
    return start, end, rest


# ---------------------------------------------------------------- headers

_STRONG_SEP = re.compile(r"\s+[|•·—–]\s+|\s+-\s+|\s*\|\s*|\n|\s{3,}|\t")


def _parts(text: str) -> list[str]:
    parts = [p.strip(" ,;:()") for p in _STRONG_SEP.split(text)]
    return [p for p in parts if p and not re.fullmatch(r"[-–—|•·,;:]+", p)]


def _split_comma(parts: list[str]) -> list[str]:
    """With only one part, try commas: "Engineer, Contoso Health, Austin, TX"."""
    if len(parts) != 1 or "," not in parts[0]:
        return parts
    bits = [b.strip() for b in parts[0].split(",") if b.strip()]
    out: list[str] = []
    i = 0
    while i < len(bits):
        # Keep "Austin, TX" together as one location.
        if i + 1 < len(bits) and is_location(f"{bits[i]}, {bits[i + 1]}") and re.fullmatch(r"[A-Z]{2}|[A-Z][a-z]+", bits[i + 1])                 and not re.fullmatch(r"(?i:remote|hybrid|on-?site)", bits[i + 1]):
            out.append(f"{bits[i]}, {bits[i + 1]}")
            i += 2
        elif re.fullmatch(r"(?:inc|llc|ltd|corp|co|gmbh|plc)\.?", bits[i], re.I) and out:
            out[-1] = f"{out[-1]}, {bits[i]}"
            i += 1
        else:
            out.append(bits[i])
            i += 1
    return out


_TRAILING_LOCATION = re.compile(r"^(?P<name>.+?),\s*(?P<loc>[A-Z][A-Za-z .'-]+,\s*[A-Z]{2}|(?i:remote|hybrid|on-?site))$")


def _peel_locations(e: Entry, parts: list[str]) -> list[str]:
    """ "Fabrikam Logistics, Portland, OR" -> "Fabrikam Logistics", with the location kept on the entry."""
    out = []
    for p in parts:
        m = _TRAILING_LOCATION.match(p)
        if m and not is_location(p) and len(parts) > 1:
            out.append(m.group("name").strip())
            e.location = e.location or m.group("loc").strip()
        else:
            out.append(p)
    return out


def parse_role_header(header: str, is_company=lambda name: False) -> Entry:
    """Title, company, location and dates from a role's header lines. `is_company(name)` says whether
    a name is a company in the user's registry, which settles which part is which.

    Lines after the one with the dates ("Official title: ...", a sentence about the company) don't
    name the role when the lines up to the dates already give a title and a company: they are noted,
    not read, so a sentence such as "... most recently as QA Manager at Example Corp." can't be taken
    for "title at company"."""
    lines = [ln for ln in header.split("\n") if ln.strip()]
    dated = next((i for i, ln in enumerate(lines) if DATE_RANGE.search(_clean(ln))), None)
    if dated is not None and dated < len(lines) - 1:
        e = _parse_role_header("\n".join(lines[: dated + 1]), is_company)
        if e.title and e.company:
            e.header = header
            extra = [_clean(ln) for ln in lines[dated + 1:]]
            note = "Also on the header line, not imported: "
            old = next((n for n in e.notes if n.startswith(note)), None)
            if old:
                e.notes.remove(old)
                extra = [old[len(note):].rstrip("."), *extra]
            e.notes.append(note + " | ".join(extra) + ".")
            return e
    return _parse_role_header(header, is_company)


def _parse_role_header(header: str, is_company) -> Entry:
    e = Entry(header=header)
    text = _clean(header.replace("\n", " | "))
    e.start, e.end, rest = take_dates(text)
    at = re.match(r"^(?P<title>.+?)\s+(?:at|@)\s+(?P<company>.+)$", rest, re.I)
    if at:
        parts = [at.group("title"), *(_parts(at.group("company")) or [at.group("company")])]
        parts = _peel_locations(e, [p.strip(" ,;:()|") for p in parts if p.strip(" ,;:()|")])
        e.title, e.company = parts[0], parts[1] if len(parts) > 1 else None
        others = parts[2:]
    else:
        parts = _split_comma(_peel_locations(e, _parts(rest)))
        others = []
        named = [p for p in parts if not is_location(p)]
        locations = [p for p in parts if is_location(p)]
        if locations:
            e.location = locations[0]
        if len(named) >= 2:
            a, b = named[0], named[1]
            others = named[2:]
            if is_company(a) and not is_company(b):
                e.company, e.title = a, b
            elif is_company(b) and not is_company(a):
                e.title, e.company = a, b
            elif TITLE_WORDS.search(b) and not TITLE_WORDS.search(a):
                e.company, e.title = a, b
            elif TITLE_WORDS.search(a) and not TITLE_WORDS.search(b):
                e.title, e.company = a, b
            elif COMPANY_WORDS.search(a) and not COMPANY_WORDS.search(b):
                e.company, e.title = a, b
            else:
                e.title, e.company = a, b
                e.notes.append(f'Read "{a}" as the title and "{b}" as the company. Swap them in base.yaml if that\'s wrong.')
        elif len(named) == 1:
            if is_company(named[0]) or (COMPANY_WORDS.search(named[0]) and not TITLE_WORDS.search(named[0])):
                e.company = named[0]
            else:
                e.title = named[0]
    if not e.location:
        for p in others:
            if is_location(p):
                e.location = p
                others.remove(p)
                break
    if others:
        e.notes.append("Also on the header line, not imported: " + " | ".join(others) + ".")
    return e


def parse_project_header(header: str) -> Entry:
    e = Entry(header=header)
    text = _clean(header.replace("\n", " | "))
    e.start, e.end, rest = take_dates(text)
    parts = _parts(rest) or ([rest] if rest else [])
    if parts:
        e.name = parts[0]
        if len(parts) > 1:
            e.description = " | ".join(parts[1:])
    return e


# ---------------------------------------------------------------- entries

def _blocks(lines: list[str]) -> list[tuple[list[str], list[str]]]:
    """Group lines into (header lines, bullet lines) entries. A new entry starts at a header line
    that follows bullets. Long plain lines after a header count as bullets (prose-style resumes)."""
    entries: list[tuple[list[str], list[str]]] = []
    header: list[str] = []
    bullets: list[str] = []
    prose_first = 0   # long plain lines right after the header, before any marked bullet

    def close() -> None:
        # When marked bullets follow, the prose before them describes the role or company (a line
        # under the header), so it goes with the header rather than becoming a bullet.
        nonlocal header, bullets, prose_first
        if prose_first and len(bullets) > prose_first:
            header, bullets = header + bullets[:prose_first], bullets[prose_first:]
        entries.append((header, bullets))
        header, bullets, prose_first = [], [], 0

    for line in lines:
        if not line.strip():
            continue
        if BULLET.match(line):
            text = _clean(BULLET.sub("", line, count=1))
            if text:
                bullets.append(text)
            continue
        clean = _clean(re.sub(r"^\s*#{1,6}\s+", "", line))
        if not clean:
            continue
        prose = len(clean.split()) > 14 and not DATE_RANGE.search(clean) and not re.match(r"^\s*#", line)
        if prose and header:
            if len(bullets) == prose_first:
                prose_first += 1
            bullets.append(clean)
            continue
        if bullets:
            close()
        header.append(clean)
    if header or bullets:
        close()
    return entries


def _education(lines: list[str]) -> list[EducationEntry]:
    out: list[EducationEntry] = []
    groups: list[list[str]] = []
    cur: list[str] = []
    for line in lines + [""]:
        if not line.strip():
            if cur:
                groups.append(cur)
                cur = []
        elif BULLET.match(line):
            if cur and not BULLET.match(cur[0]):
                groups.append(cur)
                cur = []
            groups.append([line])
        else:
            cur.append(line)
    for g in groups:
        text = " | ".join(_clean(BULLET.sub("", ln, count=1)) for ln in g)
        e = EducationEntry(text=text)
        e.start, e.end, rest = take_dates(text)
        if e.start == e.end:
            e.start = None
        parts = _split_comma(_parts(rest))
        school = next((p for p in parts if SCHOOL_WORDS.search(p)), None)
        degree_part = next((p for p in parts if p != school and DEGREE.search(p)), None)
        if degree_part is None and school and DEGREE.search(school):   # "BS in CS, Example University" in one part
            m = DEGREE.search(school)
            degree_part, school = school[m.start():].split(",")[0], None
        e.institution = school
        if degree_part:
            m = (re.match(r"^(?P<d>.+?)\s+in\s+(?P<f>[A-Z].+)$", degree_part)
                 or re.match(r"^(?P<d>.+?)\s+of\s+(?P<f>[A-Z].+)$", degree_part))
            if m and DEGREE.search(m.group("d")):
                e.degree, e.field = m.group("d"), m.group("f")
            else:
                e.degree = degree_part
        rest_parts = [p for p in parts if p not in (school, degree_part) and not is_location(p)]
        if e.degree and not e.field and rest_parts and not SCHOOL_WORDS.search(rest_parts[0]):
            e.field = rest_parts.pop(0)
        if not e.institution and rest_parts:
            e.institution = rest_parts.pop(0)
            e.notes.append(f'Read "{e.institution}" as the school. Check it in base.yaml.')
        if e.end == "present":   # still studying: education has no `present`
            e.end = None
            e.notes.append("Marked as ongoing on the resume: no end date imported.")
        out.append(e)
    return out


def _certifications(lines: list[str]) -> list[CertificationEntry]:
    out = []
    for line in lines:
        text = _clean(BULLET.sub("", line, count=1))
        if not text:
            continue
        c = CertificationEntry(text=text)
        start, end, rest = take_dates(text)
        c.date = start if start and start != "present" else None
        # "(RHCSA)" is part of the name; "(Red Hat)" names the issuer. Dashes stay: they are often
        # part of a certificate's name ("Solutions Architect – Associate").
        issuer = None
        for m in list(re.finditer(r"\(([^()]*)\)", rest)):
            inner = m.group(1).strip()
            if inner and not re.fullmatch(r"[A-Z0-9+&./-]{2,12}", inner):
                issuer = issuer or inner
                rest = rest.replace(m.group(0), " ")
        parts = [p.strip(" ,;") for p in re.split(r"\s*\|\s*|\s+—\s+|,\s+(?![^()]*\))", rest) if p.strip(" ,;")]
        c.name = parts[0] if parts else None
        c.issuer = parts[1] if len(parts) > 1 else issuer
        out.append(c)
    return out


def _skills(lines: list[str], heading: str) -> list[SkillLine]:
    out: list[SkillLine] = []
    loose: list[str] = []
    for line in lines:
        text = _clean(BULLET.sub("", line, count=1))
        if not text:
            continue
        m = re.match(r"^(?P<name>[^:]{1,60}):\s*(?P<items>.+)$", text)
        items_text = m.group("items") if m else text
        items = [i.strip(" .") for i in re.split(r"\s*[,;•·|]\s*", items_text) if i.strip(" .")]
        if m:
            out.append(SkillLine(m.group("name").strip(), items))
        else:
            loose += items
    if loose:
        out.insert(0, SkillLine(heading if heading.lower() not in ("skills",) else "Skills", loose))
    return out


def _summary(lines: list[str]) -> list[str]:
    out: list[str] = []
    para: list[str] = []
    for line in lines + [""]:
        if BULLET.match(line):
            if para:
                out += _sentences(" ".join(para))
                para = []
            text = _clean(BULLET.sub("", line, count=1))
            if text:
                out.append(text)
        elif line.strip():
            para.append(_clean(line))
        elif para:
            out += _sentences(" ".join(para))
            para = []
    return out


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text) if s.strip()]


# ---------------------------------------------------------------- the whole resume

def parse(text: str, fmt: str = "auto", is_company=lambda name: False, name: str | None = None) -> ParsedResume:
    """A resume's text as structured entries. `fmt` is html, markdown, text or auto."""
    fmt = detect_format(text, name) if fmt == "auto" else fmt
    body = html_to_lines(text) if fmt == "html" else text.replace("\r\n", "\n").replace("\r", "\n")
    out = ParsedResume()
    out.links = sorted({u.rstrip(".,;") for u in URL.findall(body)})
    if EMAIL.search(body) or PHONE.search(body):
        out.contact_dropped = True

    sections: list[tuple[str | None, str, list[str]]] = [("header", "", [])]
    for line in body.split("\n"):
        found = _section_kind(line) if line.strip() else None
        current = sections[-1][0]
        ignore = {"unknown": ("experience", "projects"),
                  "unknown_caps": ("header", "experience", "projects", "education", "certifications")}
        if found and current not in ignore.get(found[0], ()):
            kind, heading = found
            sections.append((None if kind in ignore else kind, heading, []))
        else:
            sections[-1][2].append(line)

    for kind, heading, lines in sections:
        lines = [ln for ln in lines if not _contact_only(ln)]
        if kind == "header":
            continue
        if kind is None:
            if any(ln.strip() for ln in lines):
                out.skipped_sections.append(heading)
            continue
        if kind == "summary":
            out.summary += _summary(lines)
        elif kind in ("experience", "projects"):
            previous: Entry | None = None
            for head, bullets in _blocks(lines):
                if not head:
                    out.unplaced += bullets
                    continue
                if kind == "experience":
                    e = parse_role_header("\n".join(head), is_company)
                    if e.title and not e.company and previous and previous.company and TITLE_WORDS.search(e.title):
                        e.company = previous.company
                        e.notes.append(f'No company on this entry: used "{previous.company}" from the entry above.')
                    e.bullets = bullets
                    out.roles.append(e)
                    previous = e
                else:
                    e = parse_project_header("\n".join(head))
                    e.bullets = bullets
                    out.projects.append(e)
        elif kind == "education":
            out.education += _education(lines)
        elif kind == "certifications":
            out.certifications += _certifications([ln for ln in lines if ln.strip()])
        elif kind == "skills":
            out.skills += _skills([ln for ln in lines if ln.strip()], heading)
    return out


def _contact_only(line: str) -> bool:
    """A line that is only contact details or links: never imported (contact details come from
    secrets, R5.2)."""
    rest = URL.sub("", EMAIL.sub("", PHONE.sub("", _clean(line))))
    return bool(line.strip()) and (rest != _clean(line)) and not re.search(r"[A-Za-z]{3,}", re.sub(r"\b(email|phone|tel|mobile|cell|linkedin|github|web|website)\b", "", rest, flags=re.I))
