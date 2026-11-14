"""Typed domain models (PRD A3).

These models are the single definition of every input file and record. They generate the
JSON Schemas used to validate YAML and to give editors autocomplete (R10.14), so the file
format and the database cannot drift apart.
"""

from __future__ import annotations

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
