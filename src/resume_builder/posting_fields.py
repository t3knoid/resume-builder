"""Application fields from a captured posting, each with the passage it came from (PRD R13.13–R13.15).

`extract(capture)` reads schema.org JobPosting data first, then simple rules over the posting text.
`verify(capture, fields)` is the honesty check every value must pass, whoever produced it (these
rules, a site adapter or the AI agent): the passage must appear in the capture, and the passage must
actually state the value. A field the posting doesn't state is simply absent. Nothing is guessed:
`work_arrangement` is set only when the posting says remote, hybrid or onsite, and not at all when
it says more than one of them.

Pure functions, standard library only.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit

from . import postingtext
from .errors import Problem, ValidationFailed
from .models import ExtractedField, PostingCapture

# Hosts whose postings are listings on a job board, not the employer's own site. Applicant tracking
# systems that host an employer's career pages (Greenhouse, Lever, Workday, ...) count as company_site.
JOB_BOARDS = ("indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com", "dice.com", "careerbuilder.com",
              "simplyhired.com", "usajobs.gov", "builtin.com", "wellfound.com", "angel.co", "otta.com",
              "remoteok.com", "weworkremotely.com", "flexjobs.com", "hired.com", "snagajob.com", "jobs.com",
              "idealist.org", "governmentjobs.com", "clearancejobs.com")

MONTHS = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})"), "ymd"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "mdy"),
    (re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I), "Mdy"),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTHS})\.?,?\s+(\d{{4}})\b", re.I), "dMy"),
]
MONEY = r"(?:[$£€]|USD\s?)\s?\d[\d,]*(?:\.\d+)?\s?[kK]?"
SALARY_RE = re.compile(
    rf"{MONEY}\s*(?:-|–|—|to)\s*(?:[$£€]|USD\s?)?\s?\d[\d,]*(?:\.\d+)?\s?[kK]?"
    r"(?:\s*(?:per|/|an?|a)\s*(?:year|yr|annum|hour|hr|month))?", re.I)
JOB_ID_RE = re.compile(  # "Job ID # 714052", "Req #NW-4821", "Requisition ID: R-2231"; the id must contain a digit
    r"\b(?:job\s*(?:id|number|code)|req(?:uisition)?(?:\s*(?:id|number))?|posting\s*(?:id|number))\b"
    r"\s*(?:#|:|-)?\s*#?\s*(?=[A-Z0-9_-]*\d)([A-Z0-9][A-Z0-9_-]{1,40})\b", re.I)
URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.I)

LABELS = {
    "location": ("location", "job location", "work location", "locations", "office location"),
    "external_id": ("job id", "job id #", "req id", "req #", "requisition", "requisition id", "requisition number",
                    "job number", "job requisition id", "posting id", "job code"),
    "salary_range": ("salary", "salary range", "pay range", "compensation", "base salary", "base pay range", "pay"),
    "date_posted": ("posted", "posted on", "date posted", "posting date", "post date", "published"),
    "closing_date": ("closing date", "close date", "apply by", "application deadline", "deadline", "post until",
                     "posting end date", "applications close"),
    "department": ("department", "division", "agency", "business unit"),
    "work_arrangement": ("work arrangement", "workplace type", "work type", "remote", "work location type", "work model"),
}

ARRANGEMENT_PHRASES = [
    ("remote", re.compile(r"\b(?:fully|100%|completely)\s+remote\b|\bremote[- ](?:first|only|position|role|job|opportunity|"
                          r"eligible|friendly)\b|\bwork(?:ing)? remotely\b|\bthis (?:is a|role is|position is) remote\b|"
                          r"^\s*remote\b(?:\s*[(,·|-]|\s*$)|\bremote (?:within|in) (?:the )?[A-Z]", re.I | re.M)),
    ("hybrid", re.compile(r"\bhybrid\b", re.I)),
    ("onsite", re.compile(r"\bon[- ]?site\b(?!\s+(?:interview|visit|support|training)s?)|\bin[- ]office\b|"
                          r"\bin[- ]person (?:role|position|job)\b", re.I)),
]
ARRANGEMENT_WORDS = {
    "remote": re.compile(r"\bremote(?:ly)?\b|\btelecommute\b", re.I),
    "hybrid": re.compile(r"\bhybrid\b", re.I),
    "onsite": re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|\bin[- ]person\b|\boffice[- ]based\b", re.I),
}


# ---------------------------------------------------------------- normalizing text for comparison

_DASHES = re.compile(r"[‐-―−]")


def norm(text: str) -> str:
    """Text as compared by the passage check: HTML entities decoded, Markdown emphasis and escapes
    removed, dashes unified, whitespace collapsed, case ignored."""
    t = htmllib.unescape(text).replace(" ", " ").replace("\x00", "")
    t = _DASHES.sub("-", t)
    t = re.sub(r"[*`\\]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def capture_texts(capture: PostingCapture) -> list[str]:
    """Everything a passage may be quoted from, normalized: the Markdown, the page's visible text,
    the source URL, and the JobPosting data (its string values and its JSON)."""
    texts = [capture.markdown, capture.source_url or "", capture.source_path or ""]
    raw = capture.raw_content
    if capture.content_type == "text/html":
        visible = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", raw, flags=re.S | re.I)
        texts.append(re.sub(r"<[^>]+>", " ", visible))
        for jp in postingtext.job_postings(raw):
            texts.append(_dump(jp))
            texts.extend(_strings(jp))
    else:
        texts.append(raw)
    return [norm(t) for t in texts if t]


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
        if "<" in obj:  # HTML inside JSON (a JobPosting description): its text too
            yield re.sub(r"<[^>]+>", " ", obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


# ---------------------------------------------------------------- the honesty check

def dates_in(text: str) -> list[date]:
    found = []
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(text):
            try:
                if kind == "ymd":
                    d = date(int(m[1]), int(m[2]), int(m[3]))
                elif kind == "mdy":
                    d = date(int(m[3]), int(m[1]), int(m[2]))
                elif kind == "Mdy":
                    d = datetime.strptime(f"{m[1][:3]} {m[2]} {m[3]}", "%b %d %Y").date()
                else:
                    d = datetime.strptime(f"{m[2][:3]} {m[1]} {m[3]}", "%b %d %Y").date()
            except ValueError:
                continue
            found.append(d)
    return found


def _numbers(text: str) -> set[float]:
    out = set()
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s?([kK])?", text):
        try:
            n = float(m[1].replace(",", ""))
        except ValueError:
            continue
        out.add(n * 1000 if m[2] else n)
    return out


def source_for_url(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin"
    if any(host == b or host.endswith("." + b) for b in JOB_BOARDS):
        return "job_board"
    return "company_site"


def _supports(name: str, value: str, passage: str) -> str | None:
    """None if the passage states the value; otherwise what's wrong, in plain words."""
    v, p = norm(value), norm(passage)
    if name in ("date_posted", "closing_date"):
        try:
            want = date.fromisoformat(value)
        except ValueError:
            return "a date must be written YYYY-MM-DD"
        return None if want in dates_in(passage) else f"the passage doesn't state the date {value}"
    if name == "work_arrangement":
        if value not in ARRANGEMENT_WORDS:
            return "must be remote, hybrid or onsite"
        return None if ARRANGEMENT_WORDS[value].search(passage) else f"the passage doesn't say {value}"
    if name == "source":
        if value not in ("linkedin", "job_board", "company_site"):
            return "must be linkedin, job_board or company_site"
        urls = URL_RE.findall(passage)
        return None if urls and source_for_url(urls[0]) == value else "the source must follow from the posting's link"
    if name == "posting_url":
        return None if v == p or v in [norm(u) for u in URL_RE.findall(passage)] else "the passage must be the link itself"
    if name == "salary_range":
        want = _numbers(value)
        return None if want and want <= _numbers(passage) else "the passage doesn't state those amounts"
    if name == "location":
        parts = [norm(x) for x in re.split(r"[,;/·|]", value) if x.strip()]
        return None if parts and all(x in p for x in parts) else "the passage doesn't name that location"
    return None if v and v in p else "the passage doesn't contain that value"


def verify(capture: PostingCapture, fields: list[ExtractedField]) -> list[Problem]:
    """Problems with any field whose passage isn't in the capture or doesn't state its value."""
    texts = capture_texts(capture)
    problems = []
    seen = set()
    for f in fields:
        where = f"field `{f.name}`"
        if f.name in seen:
            problems.append(Problem(f"{where} is given more than once.", field=f.name))
            continue
        seen.add(f.name)
        p = norm(f.passage)
        if not p or not any(p in t for t in texts):
            problems.append(Problem(f"The passage for {f.name} isn't in the posting, so the value can't be used.",
                                    field=f.name, example="Quote the posting's own words, e.g. \"Location: Remote, United States\""))
            continue
        why = _supports(f.name, f.value, f.passage)
        if why:
            problems.append(Problem(f"The value for {f.name} ({f.value!r}) isn't supported: {why}.", field=f.name))
    return problems


def check(capture: PostingCapture, fields: list[ExtractedField]) -> None:
    problems = verify(capture, fields)
    if problems:
        raise ValidationFailed("Some values aren't stated in the posting.", problems)


# ---------------------------------------------------------------- extraction

@dataclass
class Extraction:
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)  # structured data and text that say different things

    def put(self, name: str, value, passage: str, origin: str) -> None:
        if value is None or not str(value).strip() or not passage or not passage.strip():
            return
        value = re.sub(r"\s+", " ", str(value)).strip()[:300]
        passage = passage.strip()[:2000]
        current = self.fields.get(name)
        if current is None:
            self.fields[name] = ExtractedField(name=name, value=value, passage=passage, origin=origin)
        elif norm(current.value) != norm(value) and not _same_amounts(name, current.value, value):
            first = {"structured": "the page's structured data", "adapter": "the site adapter",
                     "agent": "the agent"}.get(current.origin, "the posting")
            self.disagreements.append(f"{name}: {first} says {current.value!r}, but its text also says {value!r}. "
                                      f"Using {current.value!r}; check it.")

    def as_list(self) -> list[ExtractedField]:
        return list(self.fields.values())


def _same_amounts(name: str, a: str, b: str) -> bool:
    return name == "salary_range" and _numbers(a) == _numbers(b) and bool(_numbers(a))


def extract(capture: PostingCapture, first: list[ExtractedField] | tuple = ()) -> Extraction:
    """Fields the posting states, structured data first (R13.14). `first` (for example a site
    adapter's fields) takes precedence over both. Every value passes `verify`; anything that doesn't
    is dropped rather than kept."""
    out = Extraction()
    for f in first:
        out.put(f.name, f.value, f.passage, f.origin)
    if capture.content_type == "text/html":
        for jp in postingtext.job_postings(capture.raw_content)[:1]:
            _from_job_posting(jp, out)
    _from_text(capture, out)
    _from_source(capture, out)
    problems = {p.field for p in verify(capture, out.as_list())}
    for name in problems:
        out.fields.pop(name, None)
    return out


def _from_job_posting(jp: dict, out: Extraction) -> None:
    def put(name, value, part):
        out.put(name, value, _dump(part) if not isinstance(part, str) else part, "structured")

    if isinstance(jp.get("title"), str):
        put("title", jp["title"], jp["title"])
    org = jp.get("hiringOrganization")
    if isinstance(org, dict) and isinstance(org.get("name"), str):
        put("company", org["name"], org["name"])
    elif isinstance(org, str):
        put("company", org, org)
    ident = jp.get("identifier")
    if isinstance(ident, dict) and ident.get("value") not in (None, ""):
        put("external_id", str(ident["value"]), ident)
    elif isinstance(ident, (str, int)):
        put("external_id", str(ident), str(ident))
    for key, name in (("datePosted", "date_posted"), ("validThrough", "closing_date")):
        if isinstance(jp.get(key), str):
            found = dates_in(jp[key])
            if found:
                put(name, found[0].isoformat(), jp[key])
    loc_type = jp.get("jobLocationType")
    if isinstance(loc_type, str) and loc_type.upper() == "TELECOMMUTE":
        put("work_arrangement", "remote", loc_type)
    location = _location(jp.get("jobLocation"))
    if location:
        put("location", location[0], location[1])
    salary = _salary(jp.get("baseSalary"))
    if salary:
        put("salary_range", salary[0], salary[1])


def _location(loc) -> tuple[str, dict] | None:
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address")
    if not isinstance(addr, dict):
        return None
    parts = [addr.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
    parts = [p if isinstance(p, str) else (p.get("name") if isinstance(p, dict) else None) for p in parts]
    parts = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    return (", ".join(parts), addr) if parts else None


def _salary(sal) -> tuple[str, dict] | None:
    if not isinstance(sal, dict):
        return None
    val = sal.get("value")
    cur = sal.get("currency") or ""
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(str(cur).upper(), f"{cur} " if cur else "")
    if isinstance(val, dict):
        lo, hi, unit = val.get("minValue"), val.get("maxValue"), val.get("unitText")
        single = val.get("value")
    else:
        lo = hi = unit = None
        single = val
    unit_text = {"YEAR": " per year", "HOUR": " per hour", "MONTH": " per month", "WEEK": " per week",
                 "DAY": " per day"}.get(str(unit or "").upper(), "")

    def money(n):
        try:
            f = float(n)
        except (TypeError, ValueError):
            return None
        return f"{sym}{f:,.0f}" if f == int(f) else f"{sym}{f:,.2f}"

    if lo is not None and hi is not None and money(lo) and money(hi):
        return f"{money(lo)} – {money(hi)}{unit_text}", sal
    if single is not None and money(single):
        return f"{money(single)}{unit_text}", sal
    return None


def _lines(markdown: str) -> list[str]:
    out = []
    for ln in markdown.split("\n"):
        ln = re.sub(r"^\s*(?:#{1,6}\s+|[-+*]\s+|\d+\.\s+|>\s*)", "", ln)
        ln = ln.replace("\\", "").replace("**", "").strip()
        if ln:
            out.append(ln)
    return out


def _from_text(capture: PostingCapture, out: Extraction) -> None:
    md = capture.markdown
    lines = _lines(md)
    labels = {lbl: name for name, lbls in LABELS.items() for lbl in lbls}

    # "Label: value", "Label | value", or a label alone on its line with the value on the next one.
    for i, ln in enumerate(lines):
        m = re.match(r"^([A-Za-z][A-Za-z #/&.-]{1,40}?)\s*(?::|\|)\s*(.+)$", ln)
        if m and m[1].strip().lower() in labels:
            _labelled(out, labels[m[1].strip().lower()], m[2].strip(), ln)
        elif ln.lower().rstrip(":") in labels and i + 1 < len(lines) and lines[i + 1].lower().rstrip(":") not in labels:
            _labelled(out, labels[ln.lower().rstrip(":")], lines[i + 1], f"{ln}\n{lines[i + 1]}")

    # Patterns anywhere in the text.
    plain = md.replace("\\", "")
    m = JOB_ID_RE.search(plain)
    if m:
        out.put("external_id", m[1], _line_of(plain, m.start()), "rule")
    m = SALARY_RE.search(md)
    if m:
        out.put("salary_range", m[0], _line_of(md, m.start()), "rule")
    m = re.search(r"\bposted(?:\s+on)?\s*:?\s*(.{6,30})", md, re.I)
    if m:
        found = dates_in(m[1])
        if found:
            out.put("date_posted", found[0].isoformat(), _line_of(md, m.start()), "rule")
    _arrangement(md, out)

    # Title: the posting's first heading; for pasted text, its first short line that isn't a link.
    heading = re.search(r"^#{1,6}\s+(.+)$", md, re.M)
    if heading:
        out.put("title", heading[1].replace("\\", "").strip(), heading[1], "rule")
    elif capture.content_type == "text/plain":
        first = next((ln for ln in lines if not URL_RE.fullmatch(ln)), None)
        if first and len(first.split()) <= 12:
            out.put("title", first, first, "rule")


def _labelled(out: Extraction, name: str, value: str, passage: str) -> None:
    value = value.strip()
    if name in ("date_posted", "closing_date"):
        found = dates_in(value)
        if found:
            out.put(name, found[0].isoformat(), passage, "rule")
    elif name == "salary_range":
        m = SALARY_RE.search(value)
        out.put(name, m[0] if m else None, passage, "rule")
    elif name == "work_arrangement":
        hits = [k for k, rx in ARRANGEMENT_WORDS.items() if rx.search(value)]
        if len(hits) == 1:
            out.put(name, hits[0], passage, "rule")
    elif name == "external_id":
        m = re.match(r"#?\s*([A-Za-z0-9][A-Za-z0-9_-]{1,40})\b", value)
        out.put(name, m[1] if m else None, passage, "rule")
    else:
        out.put(name, value, passage, "rule")


def _arrangement(text: str, out: Extraction) -> None:
    """Set work_arrangement only when the posting states exactly one arrangement."""
    hits = {}
    for kind, rx in ARRANGEMENT_PHRASES:
        m = rx.search(text)
        if m:
            hits[kind] = _line_of(text, m.start() + len(m[0]) - len(m[0].lstrip()))  # the match may start at a line break
    if len(hits) == 1:
        kind, line = next(iter(hits.items()))
        out.put("work_arrangement", kind, line, "rule")
    elif len(hits) > 1 and "work_arrangement" not in out.fields:
        out.disagreements.append("work_arrangement: the posting mentions " + " and ".join(sorted(hits))
                                 + ", so it's left unknown. Set it yourself.")


def _line_of(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else len(text)].strip()


def _from_source(capture: PostingCapture, out: Extraction) -> None:
    url = capture.source_url
    passage = url
    if not url:  # pasted text: a link in the text is the posting's link (R13.2)
        m = URL_RE.search(capture.raw_content)
        if m:
            url = passage = m[0].rstrip(".,;")
    if url:
        out.put("posting_url", url, passage, "rule")
        out.put("source", source_for_url(url), passage, "rule")
