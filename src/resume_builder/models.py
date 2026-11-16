"""Typed domain models (PRD A3).

These models are the single definition of every input file and record. They generate the
JSON Schemas used to validate YAML and to give editors autocomplete (R10.14), so the file
format and the database cannot drift apart.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, field_validator, model_validator

SCHEMA_VERSION = 1

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)]
ItemId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)]
ShortText = Annotated[str, StringConstraints(max_length=300)]
LongText = Annotated[str, StringConstraints(max_length=5000)]
def _yearish(v):
    """YAML reads `2012` as a number; accept it as the text "2012"."""
    return str(v) if isinstance(v, int) and not isinstance(v, bool) else v


YearMonth = Annotated[str, BeforeValidator(_yearish), StringConstraints(pattern=r"^\d{4}(-\d{2})?$")]
EndDate = Annotated[str, BeforeValidator(_yearish), StringConstraints(pattern=r"^(\d{4}(-\d{2})?|present)$")]
Status = Literal["draft", "confirmed"]

STAGES = (
    "interested", "tailoring", "applied", "acknowledged", "screening",
    "interviewing", "offer", "accepted", "declined", "rejected", "withdrawn", "ghosted",
)
FINAL_STAGES = {"accepted", "declined", "rejected", "withdrawn", "ghosted"}
OPEN_STAGES = [s for s in STAGES if s not in FINAL_STAGES]
Stage = Literal[
    "interested", "tailoring", "applied", "acknowledged", "screening",
    "interviewing", "offer", "accepted", "declined", "rejected", "withdrawn", "ghosted",
]
EVENT_TYPES = (
    "stage_change", "interview", "follow_up_sent", "message_received", "note", "document_sent", "correction",
)
EventType = Literal[
    "stage_change", "interview", "follow_up_sent", "message_received", "note", "document_sent", "correction",
]


class Strict(BaseModel):
    """Unknown keys are errors, so typos in YAML are caught instead of silently ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- user.yaml

class Link(Strict):
    label: ShortText
    url: ShortText


class ContactSecrets(Strict):
    email_secret: ShortText = Field("RB_CONTACT_EMAIL", description="Name of the secret or environment variable holding your email")
    phone_secret: ShortText | None = Field("RB_CONTACT_PHONE", description="Name of the secret holding your phone number; null to leave it off")


class TrackerSettings(Strict):
    stale_days: int = Field(14, ge=1, le=365, description="Days without an event before an application is flagged stale")


class WritingSettings(Strict):
    answer_max_words: int = Field(100, ge=20, le=500)
    cover_letter_min_words: int = Field(250, ge=50, le=1000)
    cover_letter_max_words: int = Field(350, ge=100, le=1500)
    banned_phrases: list[ShortText] = Field(default_factory=list)


class SnapshotSettings(Strict):
    max_per_job_and_type: int = Field(10, ge=1, le=1000)
    max_total: int = Field(1000, ge=10, le=100000)


class BackupSettings(Strict):
    keep: int = Field(30, ge=1, le=1000, description="How many backups of each kind to keep")


class IntakeSettings(Strict):
    fetch: bool = Field(True, description="false = intake never fetches a URL; paste the posting text instead")
    timeout_seconds: int = Field(20, ge=1, le=120, description="Give up on a site that takes longer than this")
    max_bytes: int = Field(2_000_000, ge=10_000, le=10_000_000, description="Refuse pages larger than this many bytes")


class PublishSettings(Strict):
    enabled: bool = False
    target: ShortText | None = Field(None, description="Where the public site goes, for example a GitHub Pages repo")


class UserSettings(Strict):
    schema_version: int = SCHEMA_VERSION
    display_name: ShortText = Field(description="Your name as it appears on documents")
    headline: ShortText | None = None
    location: ShortText | None = None
    links: list[Link] = Field(default_factory=list)
    timezone: ShortText = "UTC"
    template: Slug = "classic"
    template_version: int | None = Field(None, description="null = newest version of the template")
    contact: ContactSecrets = Field(default_factory=ContactSecrets)
    tracker: TrackerSettings = Field(default_factory=TrackerSettings)
    writing: WritingSettings = Field(default_factory=WritingSettings)
    snapshots: SnapshotSettings = Field(default_factory=SnapshotSettings)
    backups: BackupSettings = Field(default_factory=BackupSettings)
    intake: IntakeSettings = Field(default_factory=IntakeSettings)
    publish: PublishSettings = Field(default_factory=PublishSettings)


# ---------------------------------------------------------------- base.yaml

class Bullet(Strict):
    id: ItemId
    text: LongText = Field(description="The claim, outcome first. Wrap words in **double asterisks** to show them in bold.")
    tags: list[Slug] = Field(default_factory=list)
    metric: ShortText | None = Field(None, description="A real number you can stand behind. Never estimated.")
    evidence: LongText | None = Field(None, description="Where this claim comes from. Never rendered.")
    limits: LongText | None = Field(None, description="How far this claim goes. Never rendered.")
    status: Status = "draft"


class SummaryPhrase(Strict):
    id: ItemId
    text: LongText
    tags: list[Slug] = Field(default_factory=list)
    status: Status = "draft"


def _check_period(start: str | None, end: str | None) -> None:
    if start and end and end != "present" and end < start:
        raise ValueError(f"end ({end}) is before start ({start})")


class Role(Strict):
    id: ItemId
    title: ShortText
    official_title: ShortText | None = None
    company: ShortText
    location: ShortText | None = None
    start: YearMonth
    end: EndDate = "present"
    description: LongText | None = Field(None, description="One line about the employer, shown under the company line. null to leave it off.")
    bullets: list[Bullet] = Field(default_factory=list)

    @model_validator(mode="after")
    def _period(self):
        _check_period(self.start, self.end)
        return self


class Project(Strict):
    id: ItemId
    name: ShortText
    start: YearMonth | None = None
    end: EndDate | None = None
    description: LongText | None = None
    bullets: list[Bullet] = Field(default_factory=list)

    @model_validator(mode="after")
    def _period(self):
        _check_period(self.start, self.end)
        return self


class Education(Strict):
    id: ItemId
    institution: ShortText
    degree: ShortText | None = None
    field: ShortText | None = None
    start: YearMonth | None = None
    end: YearMonth | None = None
    status: Status = "draft"


class Certification(Strict):
    id: ItemId
    name: ShortText
    issuer: ShortText | None = None
    date: YearMonth | None = None
    status: Status = "draft"


class SkillGroup(Strict):
    id: ItemId
    name: ShortText
    items: list[ShortText] = Field(default_factory=list)


class ContentLibrary(Strict):
    schema_version: int = SCHEMA_VERSION
    summary_phrases: list[SummaryPhrase] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)

    def all_bullets(self) -> dict[str, Bullet]:
        out: dict[str, Bullet] = {}
        for r in self.roles:
            for b in r.bullets:
                out[b.id] = b
        for p in self.projects:
            for b in p.bullets:
                out[b.id] = b
        return out

    def all_ids(self) -> list[str]:
        ids = [s.id for s in self.summary_phrases] + [r.id for r in self.roles] + [p.id for p in self.projects]
        ids += [e.id for e in self.education] + [c.id for c in self.certifications] + [s.id for s in self.skills]
        ids += list(self.all_bullets().keys())
        return ids

    def all_tags(self) -> set[str]:
        tags: set[str] = set()
        for b in self.all_bullets().values():
            tags.update(b.tags)
        for s in self.summary_phrases:
            tags.update(s.tags)
        return tags


# ---------------------------------------------------------------- organizations.yaml

class Department(Strict):
    slug: Slug
    name: ShortText
    aliases: list[ShortText] = Field(default_factory=list)
    notes: LongText | None = None


class Company(Strict):
    slug: Slug
    name: ShortText
    aliases: list[ShortText] = Field(default_factory=list)
    website: ShortText | None = None
    notes: LongText | None = None
    departments: list[Department] = Field(default_factory=list)


class OrganizationRegistry(Strict):
    schema_version: int = SCHEMA_VERSION
    companies: list[Company] = Field(default_factory=list)


# ---------------------------------------------------------------- profiles

class OmittedRole(Strict):
    role: ItemId
    reason: ShortText


class Gap(Strict):
    skill: ShortText
    note: ShortText | None = None


class Profile(Strict):
    """Selections and order only. No new resume text (R4.3)."""

    schema_version: int = SCHEMA_VERSION
    summary: list[ItemId] = Field(default_factory=list)
    roles: dict[ItemId, list[ItemId]] = Field(default_factory=dict, description="role id -> bullet ids, in order")
    projects: dict[ItemId, list[ItemId]] = Field(default_factory=dict)
    education: list[ItemId] = Field(default_factory=list)
    certifications: list[ItemId] = Field(default_factory=list)
    skills: list[ItemId] = Field(default_factory=list, description="skill group ids, in order")
    keywords: list[Slug] = Field(default_factory=list, description="posting keywords this profile claims; each must be a tag")
    earlier_roles: dict[ItemId, list[ItemId]] = Field(
        default_factory=dict,
        description="Older roles condensed into one 'Earlier experience' entry: role id -> bullet ids (often none), newest first")
    omitted_roles: list[OmittedRole] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)


# ---------------------------------------------------------------- application.yaml

class Contact(Strict):
    name: ShortText
    role: Literal["recruiter", "hiring_manager", "interviewer", "referrer", "other"] = "other"
    channel: ShortText | None = None
    notes: LongText | None = None


class Interview(Strict):
    round: int | None = Field(None, ge=1, le=20)
    format: Literal["phone", "video", "onsite", "take_home"] | None = None
    interviewers: list[ShortText] = Field(default_factory=list)
    assessment: LongText | None = None


class Event(Strict):
    date: date
    type: EventType
    stage: Stage | None = Field(None, description="Required when type is stage_change")
    contact: ShortText | None = None
    notes: LongText | None = None
    documents: list[ShortText] = Field(default_factory=list, description="snapshot ids, for example of the resume you sent")
    interview: Interview | None = None
    corrects: int | None = Field(None, ge=1, description="For type correction: the number of the event being corrected (1 = first)")

    @model_validator(mode="after")
    def _shape(self):
        if self.type == "stage_change" and not self.stage:
            raise ValueError("a stage_change event needs a `stage`")
        if self.type == "correction" and not self.corrects:
            raise ValueError("a correction event needs `corrects` (the number of the event it corrects)")
        return self


class Submission(Strict):
    applied_via: ShortText | None = None
    resume_snapshot: ShortText | None = None
    cover_letter_snapshot: ShortText | None = None
    answer_snapshots: list[ShortText] = Field(default_factory=list)
    referral: ShortText | None = None


class Application(Strict):
    schema_version: int = SCHEMA_VERSION
    job_id: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)]
    company: Slug
    department: Slug | None = None
    title: ShortText | None = None
    posting_url: ShortText | None = None
    source: Literal["linkedin", "referral", "company_site", "recruiter", "job_board", "other"] | None = None
    external_id: ShortText | None = None
    location: ShortText | None = None
    work_arrangement: Literal["remote", "hybrid", "onsite", "unknown"] | None = None
    salary_range: ShortText | None = None
    date_posted: date | None = None
    closing_date: date | None = None
    interest_level: int | None = Field(None, ge=1, le=5)
    interest_notes: LongText | None = Field(None, description="Why you want this job. The only source of motivation in letters and answers.")
    fit_summary: LongText | None = None
    next_action: ShortText | None = None
    next_action_due: date | None = None
    outcome_reason: ShortText | None = None
    submission: Submission = Field(default_factory=Submission)
    contacts: list[Contact] = Field(default_factory=list)
    notes: LongText | None = None
    events: list[Event] = Field(default_factory=list)

    @field_validator("events")
    @classmethod
    def _events_present(cls, v):
        if not v:
            raise ValueError("needs at least one event (the first is the stage_change to `interested`)")
        return v


# ---------------------------------------------------------------- captured postings

CaptureSource = Literal["url", "file", "stdin"]
CaptureContentType = Literal["text/html", "text/plain", "application/json"]  # JSON: site adapters only (D31)


class PostingCapture(BaseModel):
    """A job posting exactly as it was fetched or pasted (PRD R13.10). Never edited after it is stored.

    Not a YAML input, and deliberately not `Strict`: whitespace in the captured text is kept as is.
    `id`, `content_hash`, `created_by` and `created_at` are set by storage.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    job_id: str | None = Field(None, description="NULL until the intake is confirmed and the job exists")
    source_kind: CaptureSource
    source_url: str | None = None
    source_path: str | None = Field(None, description="Relative to the user's data folder, for source_kind file")
    content_type: CaptureContentType
    raw_content: str = Field(description="The page or text exactly as received")
    markdown: str = Field(description="The posting converted mechanically to Markdown, wording unchanged")
    content_hash: str | None = Field(None, description="sha256 of raw_content, hex")
    captured_at: str = Field(description="When it was fetched or read, ISO 8601")
    created_by: str | None = None
    created_at: str | None = None

    @model_validator(mode="after")
    def _source(self):
        if self.source_kind == "url" and not self.source_url:
            raise ValueError("a capture from a URL needs `source_url`")
        if self.source_kind == "file" and not self.source_path:
            raise ValueError("a capture from a file needs `source_path`")
        return self


class UserAnswers(Strict):
    """Application fields only the user can know, in the user's own words (issue #38).

    Never checked against a posting. When the AI agent passes them on (`from_user` in a job
    proposal, or a drafted change), they wait for the owner, who checks they are really theirs.
    """

    interest_level: int | None = Field(None, ge=1, le=5, description="1 (meh) to 5 (dream job)")
    interest_notes: LongText | None = Field(None, description="Why the user wants this job, in their own words, "
                                                              "word for word. The only source of motivation in letters.")
    source: Literal["linkedin", "referral", "company_site", "recruiter", "job_board", "other"] | None = Field(
        None, description="How the user found the job")
    next_action: ShortText | None = Field(None, description='e.g. "Follow up with recruiter"')
    next_action_due: date | None = Field(None, description="YYYY-MM-DD")
    notes: LongText | None = Field(None, description="Private notes. Never on any document.")


class ApplicationChanges(UserAnswers):
    """The application fields that can be changed after a job exists (issue #38). Only the fields
    given are changed; a field given as null is cleared."""

    fit_summary: LongText | None = Field(None, description="Short note on fit, often from the fit report")
    outcome_reason: ShortText | None = Field(None, description="Why it ended, once it has")


USER_ANSWER_FIELDS = tuple(UserAnswers.model_fields)
CHANGEABLE_FIELDS = tuple(ApplicationChanges.model_fields)
UserAnswerName = Literal["interest_level", "interest_notes", "source", "next_action", "next_action_due", "notes"]


IntakeFieldName = Literal["title", "company", "department", "posting_url", "source", "external_id", "location",
                          "work_arrangement", "salary_range", "date_posted", "closing_date"]
FieldOrigin = Literal["structured", "rule", "adapter", "agent"]


class ExtractedField(BaseModel):
    """One application field taken from a captured posting, with the passage it came from (PRD R13.15).

    The passage must appear in the capture and must support the value; `posting_fields.verify`
    checks both, whoever proposed the value.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: IntakeFieldName
    value: Annotated[str, StringConstraints(min_length=1, max_length=300)] = Field(
        description="As stated in the posting. Dates as YYYY-MM-DD; work_arrangement remote | hybrid | onsite; "
                    "source linkedin | job_board | company_site")
    passage: Annotated[str, StringConstraints(min_length=1, max_length=2000)] = Field(
        description="The exact text from the posting that states the value")
    origin: FieldOrigin = "rule"


def content_hash(text: str) -> str:
    """The hash stored with a capture: sha256 of the UTF-8 text, hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- documents

DOC_KINDS = ("fit_report", "cover_letter", "answer")
DocKind = Literal["fit_report", "cover_letter", "answer"]


class DocumentDraft(Strict):
    kind: DocKind
    text: Annotated[str, StringConstraints(max_length=20000)]
    question: ShortText | None = Field(None, description="For answers: the question being answered")
    slug: Slug | None = Field(None, description="For answers: short name, e.g. why-here")


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.\-%$]*", text))
