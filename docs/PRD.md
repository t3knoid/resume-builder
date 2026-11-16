# Product Requirements: resume-builder

*November 13, 2026. Status: **approved**, after a consistency review. Last updated November 14, 2026. Changes are recorded in [CHANGELOG.md](../CHANGELOG.md).*

---

## 1. Summary

resume-builder helps a job seeker do two things:

1. **Tailor honestly.** Keep one complete, evidence-backed record of their career. For each job, produce a resume that selects and orders that record. The AI can choose and rank content but cannot invent claims.
2. **Track applications.** Record every application from first interest to final outcome in a small SQLite database. A local website shows the full history of each application, including which resume version was sent.

It is structured from day one so that anyone can use it by adding their own content folder, without touching code and without their data mixing with anyone else's.

## 2. Problem

- **One resume for every job hides the most relevant evidence.** Tailoring by hand is slow, so people skip it.
- **AI tailoring makes claims up.** It inflates experience, invents tools and metrics, and drifts into generic corporate language. Most users can't easily tell what the AI added.
- **Job searches are hard to keep track of.** After 20+ applications, people lose track of what they sent, to whom, when they last heard back and what happened. Spreadsheets break down, and nothing ties the outcome back to the resume that was sent.
- **Nothing links tailoring to results.** Job seekers can't answer "which version of my resume gets interviews?"

## 3. Goals

| #  | Goal                                                                                                                            | Measured by                                                                                                            |
| -- | ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| G1 | Tailored resume (plus optional cover letter and "why here" answer) from a pasted posting in under 15 minutes of the user's time | Time from `posting.md` created to PDF approved                                                                         |
| G2 | Zero invented claims                                                                                                            | Validator passes; every rendered line traces back to a content library id                                              |
| G3 | Every application tracked from start to outcome                                                                                 | Share of applications with a final status or a dated next action                                                       |
| G4 | The user can see the full history of any application                                                                            | Timeline view shows every status change, the resume sent and contacts                                                  |
| G5 | A new user can onboard by adding content only                                                                                   | New user gets a first PDF without editing any file outside `data/<user>/`, apart from copying `.env.example` to `.env` |
| G6 | Users learn from their own search                                                                                               | Dashboard shows response rate, time to response and stage conversion                                                   |

## 4. Users

- **Primary: technical job seekers.** Comfortable with YAML, git and the command line. Running an active search, want editorial control over AI output and strict honesty in what they send.
- **Possible later: reviewers.** Career coaches, mentors or friends who read and comment on a user's materials without changing them (enabled by RBAC, section 9.2).

The first real user is onboarded through the same path as any other user (`init-user`, then their own `data/<user>/` folder). The product has no special cases for any user.

## 5. Product principles

1. **The user's evidence is the only source of claims.** If it isn't in the user's content library, it isn't on the resume.
2. **AI advises; the user decides.** Every AI output is reviewed before use.
3. **Content and code are separate.** Code never contains a user's name, employer, contact details or job data. Everything specific to one user lives in that user's data folder and database.
4. **Local-first and private by default.** Job data, tracker data and the tracker website never reach a public location unless the user explicitly publishes something.
5. **YAML in, SQLite as the record.** Users enter data by editing readable YAML and Markdown files. Once validated and synced, that data lives in SQLite, which is the app's only source of truth (section 8.7).
6. **Everything goes through the API.** Every read and write of user data goes through one service API that checks permissions. No part of the app opens a data file or runs SQL directly (section 9).

## 6. Scope

### 6.1 What the app does

- Folder layout separated by user (section 8.1), with a fictional example user for tests and docs.
- Dockerfile and `compose.yaml` with the data, database, output and backup mounts (section 11). The app runs in Docker from the first milestone.
- `bootstrap` script that finds, creates and repairs user folders on every start, with self-explanatory seeded files and smart defaults (section 8.10). `init-user` and `new-job` commands. A `user.yaml` holding everything that differs between users: display name, publish target, names of the contact-detail secrets, template, limits and thresholds.
- Core architecture (section 9): domain models, service API with request context and permission checks, SQLite storage with schema migrations, generated HTTP API, CLI as a thin client.
- **YAML sync** (section 8.7): validated, change-aware sync of the user's YAML and Markdown input into SQLite, write-back of app changes to the files, conflict detection, automatic database backups.
- Content library with ids, tags, metrics, evidence and limits.
- Import of an existing resume (Word, PDF, HTML, Markdown or plain text, from a file or a link) as draft content library entries, so a new user doesn't start `base.yaml` from scratch (R2.6–R2.12).
- For each job: posting, AI-generated profile, fit report, application record.
- Job intake (section 8.13): start a job from its posting URL, or from pasted text as a fallback, with fields extracted and the posting captured, confirmed once by the user.
- Validator, renderer, ATS-friendly HTML and PDF output (WeasyPrint).
- Cover letter and "Why do you want to work here?" drafts built from the tailored resume (section 8.9).
- Local tracker website: application list, history for each application, basic dashboard, sync status, AI agent status (R8.10), online help (R8.11), copy-ready commands (R8.12) and ready-made prompts (R8.13). Read-only unless the owner signs in, which adds Confirm and Discard for what the agent proposed and an Edit details form for the owner's own fields on a job (R8.8).
- Optional public resume site: render the public profile and export it as a static site. Pushing it to the publish target in `user.yaml` (for example a GitHub Pages repo) is milestone 6.
- A user manual and a quick-start guide (`docs/USER-MANUAL.md`, `docs/QUICKSTART.md`), written around the example user.
- **No built-in AI model.** The AI agent is an outside tool the user runs. It always acts as the `agent` actor (B7, DK13) through one of four channels, each going through the operation registry and the same checks:
  - **MCP server** (`rb mcp`, stdio): tools generated from the registry, filtered to the agent role's permissions. Works with MCP clients such as Claude Code, Claude Desktop, Gemini CLI, Codex CLI and GitHub Copilot in VS Code.
  - **MCP over HTTP** (`POST /mcp/<user>` on the tracker, D33): the same tools for assistants on the user's computer that connect with a URL. Needs the agent's token (B9a), answers plain JSON, refuses a foreign `Origin` as well as a foreign Host (R8.9). Local only: cloud connectors can't reach it.
  - **Copy and paste** for chat-only assistants: `rb brief` / `rb agent-instructions` produce the text to paste; `rb propose` submits each returned block as the agent.
  - **HTTP API** with an agent token, for scripts.
  - Per-assistant setup instructions are in `docs/AI-AGENTS.md`.

### 6.2 Building on resume-builder

Other packages can embed or extend the app through the interfaces described in [ARCHITECTURE.md](ARCHITECTURE.md), without changing this code. To keep that possible:

- Don't let any code path assume a single user per process or read user data outside a request context.
- Don't add file-only features without an operation behind them; a host may have no user files.
- Keep the honesty checks in our own code (not in prompts), so they apply to any AI agent.
- When an outside package needs something the app doesn't offer, add a generic extension point, not a special case.

## 8. Functional requirements

### 8.1 Folder layout and separating users

| Id | Requirement |
| --- | --- |
| R1.1 | Every command acts on one user (`--user <name>`, defaulting to `RB_DEFAULT_USER`) and reads and writes only that user's folder and database. |
| R1.2 | No code path reads another user's folder or database. Ids only need to be unique within one user. |
| R1.3 | **Job id format:** `YYYY-MM-DD-<company>[-<department>]`. The date is the day the job was first entered, in the user's time zone. Company and department are the slugs of the organization shown in the job description, taken from the organization registry (section 8.11). Example: `2026-11-13-acme-corp-platform-engineering`.<br>• The app builds the id from the date, company and department. The user picks those values; they never type the id by hand.<br>• Each slug is lowercase letters, digits and hyphens. The whole id is at most 80 characters, with the department slug shortened first if needed.<br>• If the same id already exists for that user, the app adds `-2`, `-3` and so on.<br>• The id never changes after creation, even if the company or department is later corrected. The application record keeps the current values.<br>• Job ids contain employer names, so they follow the privacy rules for employer names (section 10). |
| R1.4 | The data folder holds only text: the user's input files and the app's write-back of current data (R7.12). The database in `/db` is the source of truth and holds everything else, including snapshots and the audit log. Rendered PDFs and HTML are never stored (section 8.12). |
| R1.5 | Tests, docs and examples use only the fictional `examples/sample-user/`. Real user data never appears in tests, fixtures, docs or code. |
| R1.6 | No user gets special treatment in code. A need that comes from one user becomes a generic feature, configured in `user.yaml`, or it stays in that user's data. |
| R1.7 | The four roots are set by environment variables: `RB_DATA_ROOT`, `RB_DB_ROOT`, `RB_OUTPUT_ROOT` and `RB_BACKUP_ROOT`. In the container they default to `/data`, `/db`, `/output` and `/backups`. User data never has to live inside the code repo (section 11). |

**Code repo** (contains no user data):

```text
resume-builder/
  CLAUDE.md
  Dockerfile
  compose.yaml              # volumes, localhost-only port, secrets
  .dockerignore             # keeps data/, output/, backups/ and secrets out of the image
  docs/                     # PRD, user guide
  src/                      # shared code: no user content
    resume_builder/
      templates/            # shared resume and letter templates, plus the seed templates (packaged)
      migrations/           # versioned SQLite schema migrations (packaged)
  examples/
    sample-user/            # fictional user: same layout as a user data folder, used by tests and docs
  tests/
  data/                     # default host location for user data (gitignored)
  output/                   # default host location for exports (gitignored)
  backups/                  # default host location for database backups (gitignored)
```

**Container mounts** (section 11):

```text
/data     ← bind mount of the host data folder      user input files (YAML/Markdown), one folder per user
/db       ← named Docker volume                      SQLite databases, the source of truth, one per user
/output   ← bind mount of the host output folder     temporary exports of rendered PDFs/HTML (disposable)
/backups  ← bind mount of the host backups folder    database backups (automatic and on demand)
```

**One user's files:**

```text
/data/
  <user>/                   # input and working copies; can be its own private git repo
    .schemas/               # generated JSON Schemas for this user (they include the user's registry slugs)
    START-HERE.md           # short checklist
    SYNC-STATUS.md          # written by the app: last sync result and any problems, file by file
    user.yaml               # settings: display name, publish target, secret names, template, limits
    base.yaml               # content library input (BASE CV + evidence)
    organizations.yaml      # companies and their departments, reused across jobs
    profiles/
      public.yaml           # selection for the public site
    intake/                 # posting text saved from a browser for job intake; read only on command (R13.4)
      README.md
    jobs/
      <job-id>/
        posting.md          # the posting, captured by intake or pasted
        application.yaml    # tracker record and event log
        profile.yaml        # working copy of the AI-generated selection (ids only)
        fit-report.md       # working copy of the fit report
        cover-letter.md     # optional: working copy of the drafted letter
        answers/
          why-here.md       # optional: working copy of the "Why do you want to work here?" answer
/db/
  <user>/
    app.db                  # SQLite: source of truth for everything, including snapshots, audit log and sync state
/backups/
  <user>/                   # timestamped copies of app.db
/output/
  <user>/
    <job-id>/               # temporary PDF/HTML exports, rebuilt from snapshots on demand
    public/
```

### 8.2 Content library (`base.yaml`)

| Id | Requirement |
| --- | --- |
| R2.1 | Contains summary phrases, roles, bullets, projects (including personal or lab work, with dates), education, certifications and skills. |
| R2.2 | Every bullet has: `id`, `text`, `tags`, `metric` (null until the user supplies one), `evidence`, `limits`, `status` (`draft` \| `confirmed`). |
| R2.3 | Only `confirmed` bullets can appear in a rendered resume. The one exception is a **preview** (`render --preview`), which also includes drafts and prints "PREVIEW: contains draft content" on every page. Preview snapshots can never be approved, sent or published. |
| R2.4 | `evidence` and `limits` are never rendered. |
| R2.5 | Summary phrases, education, certifications and skill groups also have a `status`, and the same rule applies: only `confirmed` ones render outside a preview. A skill group without a `status` is `confirmed`, because skill groups had no status before R2.6; the seed's example group is a draft. |

#### Importing an existing resume (issue #1, D38)

| Id | Requirement |
| --- | --- |
| R2.6 | `import-resume` reads an existing resume in Word (.docx), PDF, HTML, Markdown or plain text and adds what it finds to the content library: summary phrases, roles with their bullets, projects, education, certifications and skill groups. The resume is saved in the user's intake folder (`rb import-resume intake/resume.html`), piped in (`--stdin`), or given as a link (R2.11). Word and PDF files come only from the intake folder (R2.12). Operations: `library_import` (text or a link) and `library_import_file` (a file in the intake folder, read by the sync adapter and never changed), both `content:propose`, both a dry run unless told otherwise. The CLI shows the result and asks once before adding anything. Each result carries a `content_hash` of what was read; an import given `expect_hash` is refused if the resume changed since that preview, so nothing is added that the user didn't see. |
| R2.7 | **Honesty:** every imported bullet, summary phrase, education, certification and skill group is a `draft`; only the owner confirms it (`rb confirm`, including `--all`), so nothing imported renders until then (R2.3, R2.5). The resume's own words are kept: nothing is reworded, and no tag, metric, date or title is added that the resume doesn't state. `metric` stays null; bullets that mention numbers are listed so the user can fill in `metric` only where they can stand behind it. |
| R2.8 | **Never overwrites:** existing entries are never changed. New ids are generated (unique among current and archived ids). Likely duplicates of existing bullets, summary phrases, education, certifications and skill groups are reported, not imported or merged; a skill group that exists gets a note listing the items it doesn't have. New bullets for a role already in the library (same company and start date) or a project with the same name are added under it as drafts. |
| R2.9 | **Plain parsing, no built-in AI:** sections are found by their headings, entries by their header lines and bullets. Where the parser has to choose, for example which part of a header is the title and which the company, the report says so. A role with no title, company or start date isn't imported; the report says why. Contact details (email, phone) are never imported (R5.2); links are listed for `user.yaml`; sections the library doesn't hold (awards, languages, ...) are named as left out. |
| R2.10 | **Companies:** each role's company is matched against the organization registry by name and alias (R11.4). A match uses the registry's name; a company with no match is suggested with the command that adds it (`rb add-company`), never created (R11.5). The report lists everything added, the likely duplicates, what was skipped and why, and what needs checking. An outside agent can help through the same operation: it may import text or a link the user gave it, but only as drafts (B7). |
| R2.11 | **From a link:** `rb import-resume <link>` (operation `library_import` with `url`) fetches the resume once through the App's fetcher with the same safety as job intake (R13.5–R13.8: one GET, no cookies or user data, internal addresses refused on every redirect, the user's `intake` timeout and size limit, `intake.fetch: false` turns it off), audited as `resume:fetch` with the host only. Web pages, Markdown and plain text are read; `text/markdown` counts as text. A Google Docs share link is read through the document's export link (it works only for a doc shared with anyone who has the link). A sign-in page, a page that needs JavaScript or one with too little text is refused, with the fallback of saving the resume in the intake folder. Only `http` and `https` links. |
| R2.12 | **Word and PDF (issue #39, D39):** `library_import_file` reads `.docx` and `.pdf` files, told apart by their first bytes and their name. Word is converted to HTML with `mammoth`, keeping headings, Word's lists, bold and tables in reading order (images dropped), and read by the same HTML reader. A PDF's text layer is extracted with `pdfminer.six` in a separate process with a time limit (30 s) and a page limit (20); bullet marks and wrapped lines are put back together, without changing a word. Limits: 10 MB per file; a `.docx` with more than 1,000 parts or unpacking to more than 50 MB is refused before conversion (zip bombs). Refused with what to do instead: the old binary `.doc` format (save as `.docx`), PDFs without text (scans: no text recognition), password-protected and damaged files. The report reminds the user to check a PDF's order and bullets (two-column layouts), and says so when little was recognized. Conversion runs only on the bytes the sync adapter read, never on a path or a link, and never reaches the network. Word and PDF files can't be piped in or given as a link. |

### 8.3 Tailoring

| Id | Requirement |
| --- | --- |
| R3.1 | The user adds `posting.md`. The AI produces a profile (ids and order only) and a fit report through the API. Both are stored in the database and written back as `profile.yaml` and `fit-report.md` for review. |
| R3.2 | The fit report lists strong matches, weak spots, gaps, questions for the user, and notes on the role. |
| R3.3 | Each gap gets a decision from the user: a real gap, or missing information to add to the content library as a draft. |
| R3.4 | **Condensing older roles:** a profile puts recent, relevant roles under `roles` and condenses older or less relevant ones under `earlier_roles` (role id → a few bullet ids, or none), instead of dropping them, so the work history has no unexplained gap. The resume shows them after the main roles as one "Earlier experience" entry: each role's own title, company and dates, plus any bullets chosen. Every role under `earlier_roles` is older than the roles under `roles`, and a role can't be in both. Bullets under `earlier_roles` are on the tailored resume, so letters and answers may cite them (R9.1). |
| R3.5 | **Agent brief:** `agent_brief` (`rb brief`) gives the agent everything it may use for one job, and nothing else: the agent rules, the user's word limits and banned phrases, how to submit drafts, the job's company, title and `interest_notes`, the posting, and the confirmed content library: summary phrases, roles and projects with their dates and confirmed bullets (tags, metric, limits), skill groups, and every recorded detail of confirmed education (school, degree, field, dates) and certifications (name, issuer, date), so the agent never asks for, or guesses, what the library already holds. Draft items are named only as not usable yet. |

### 8.4 Content rules

These rules apply to every write, whether it arrives through YAML sync (section 8.7) or the API.

| Id | Requirement |
| --- | --- |
| R4.1 | Every referenced id exists and is `confirmed`. |
| R4.2 | Every keyword the profile claims maps to a tag in the content library. |
| R4.3 | A profile contains no free text, except in `gaps`. |
| R4.4 | Roles stay in chronological order, under `roles` and under `earlier_roles` (R3.4). A role is left out only if it is listed in `omitted_roles` with a reason. |
| R4.5 | Each application matches its schema, its event dates run in order, and its status transitions are allowed (section 8.6). |

### 8.5 Rendering

| Id | Requirement |
| --- | --- |
| R5.1 | The content library plus a profile produces resume HTML, then the ATS HTML, then a PDF. |
| R5.2 | Contact details come from secrets or environment variables named in `user.yaml`. They are never committed or stored. |
| R5.3 | Every render first creates a snapshot (section 8.12). Its id is the resume version id, so a submission can be traced to exactly what was sent and rebuilt exactly. |
| R5.4 | Golden-file tests render the example user's profiles and compare the result with expected HTML. |
| R5.5 | Templates contain no user content. Name, headings and contact details come from `user.yaml` and secrets. |
| R5.6 | Rendering reads only from the database (through the API), never from the YAML files directly. |

### 8.6 Application tracker

Each application is stored in the database with fixed fields plus an **append-only event log**. `application.yaml` is the way the user enters and edits it. The history view is built from the event log in the database.

#### Application fields

| Group      | Fields                                                                                                                                                                                                                                                                              |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Posting    | company and department (references to the organization registry, section 8.11), title, posting URL, source (LinkedIn, referral, company site, recruiter), external posting id, location, work arrangement (remote/hybrid/onsite), salary range as posted, date posted, closing date |
| Fit        | summary of matched requirements and gaps (from the fit report), user's interest level (1–5), `interest_notes` (why the user wants this job; the only source of motivation in letters and answers)                                                                                   |
| Submission | date applied, how they applied, snapshot ids of the resume, cover letter and application answers that were sent (section 8.12), referral contact                                                                                                                                    |
| Status     | current stage (derived from the latest event), next action and its due date, final outcome and reason                                                                                                                                                                               |
| People     | contacts: name, role (recruiter, hiring manager, interviewer, referrer), channel, notes                                                                                                                                                                                             |
| Notes      | free-form notes. Never rendered on a resume.                                                                                                                                                                                                                                        |

#### Lifecycle stages

```text
interested → tailoring → applied → acknowledged → screening → interviewing → offer → accepted
                                                                                  ↘ declined
From any open stage                  → withdrawn
From applied or any later open stage → rejected | ghosted
```

`accepted`, `declined`, `rejected`, `withdrawn` and `ghosted` are final. A final stage can be left only by a correction event (R6.4).

| Id | Requirement |
| --- | --- |
| R6.1 | Each event has: date, type (stage change, interview, follow-up sent, message received, note, document sent), and optional stage, contact, notes and linked documents. |
| R6.2 | Interview events record round, format (phone, video, onsite, take-home), interviewers and the user's own assessment. |
| R6.3 | An application is flagged **stale** when it has had no event for N days (default 14, set in `user.yaml`). The user decides whether to mark it `ghosted`. The system never changes a stage by itself. |
| R6.4 | Events are never edited in place to change history. A correction is recorded as a new event that refers to the old one. Sync rejects YAML edits that change or remove an event already in the database (R7.9). |
| R6.5 | The AI never makes up events, dates or contacts. It may draft an event from something the user pastes (for example a rejection email), for the user to confirm. |
| R6.6 | **The user's own fields (D37):** interest level, `interest_notes`, source, next action and its due date, notes, fit summary and outcome reason can be changed through an operation, not only in `application.yaml`: `application_update` (owner, `application:write`), as `rb set <job-id>`, through the HTTP API, and with an **Edit details** form on the job page once the owner signs in (R8.8). Only the fields given change; one given as empty is cleared. The stage, events, submission and ids never change this way. The change is written back to `application.yaml`, keeping comments. |
| R6.7 | The agent may **propose** a change to those fields (`application_propose_update`, `application:propose`), stored as a draft with the values before and after. Interest notes and the other answers must be the user's own words, word for word. The owner confirms or discards it (`rb confirm-update`, `rb discard-update`, or on the job page); confirming fails if a field changed after the draft was made. Drafts are kept, never edited, and resolved once (migration 0006). |

### 8.7 SQLite database and YAML sync

SQLite is the source of truth. The user's YAML and Markdown files are the input channel: changes in them are validated and then synced into the database. After a successful sync, the app uses only the database.

#### Database

| Id | Requirement |
| --- | --- |
| R7.1 | Each user has one SQLite database, `/db/<user>/app.db`. It holds all application data: content library, organizations, profiles, applications, events, contacts, letters and answers, snapshots, the audit log and sync state. |
| R7.2 | Main tables: `content_items` (summary phrases, roles, bullets, projects, skills), `companies`, `departments` (each belongs to a company), `profiles`, `applications` (references company and department), `events`, `contacts`, `documents` (letters, answers, fit reports), `snapshots` (canonical JSON plus id, job, document type, created time, protection), `snapshot_bullets`, `audit_log`, `sync_files` (one row per input file: path, last-synced content and hash, schema version, time, result), `sync_runs`. |
| R7.3 | Integrity is enforced in the database as well as in code: foreign keys on, `CHECK` constraints for every allowed value (stages, statuses, event types), `UNIQUE` constraints on ids within a user, `NOT NULL` where a field is required. SQLite runs in WAL mode. |
| R7.4 | The schema is versioned. Migrations in `src/resume_builder/migrations/` run automatically at startup, after a backup, and never lose data. `doctor` runs SQLite's integrity check. |

#### When sync runs

| Id | Requirement |
| --- | --- |
| R7.5 | Sync runs:<br>• at startup, as part of `bootstrap`<br>• while the app is running, whenever an input file changes (a file watcher, falling back to polling every few seconds where file events don't cross the Docker mount)<br>• before any CLI command that reads data, if a file changed since the last sync<br>• on demand with `sync` (`sync --check` validates without applying) |
| R7.5a | Only one sync runs at a time for a user. A per-user lock stops the watcher and a CLI command from syncing at once; the second one waits, then finds nothing to do. |
| R7.6 | **Change detection:** each input file's content hash is compared with the hash stored at its last successful sync. Unchanged files are skipped, so sync is fast and does nothing when nothing changed. |

#### Validation before anything reaches the database

| Id | Requirement |
| --- | --- |
| R7.7 | Each changed file passes these stages in order. A file that fails any stage is not applied.<br>1. **Safe reading:** UTF-8 only; size limit per file (default 1 MB); YAML read with a safe loader (no custom tags, no code execution); duplicate keys rejected; limits on nesting depth and alias expansion, so a malformed or hostile file can't exhaust memory; control characters rejected.<br>2. **Schema:** the file matches the schema generated from the domain models (A3): required fields, types, allowed values, id and slug patterns, ISO dates, maximum text lengths. Older `schema_version` values are migrated in memory first.<br>3. **Content rules:** the rules in section 8.4 and R1.3.<br>4. **References:** every referenced id, company and department exists, checked against the database plus the other files in the same sync run.<br>5. **History rules:** no change or removal of events already in the database (R6.4); no change of an existing job id (R1.3).<br>6. **Merge check:** a three-way comparison against the last-synced version of the file and the current database (R7.11).<br>7. **Database dry run:** the changes are applied through the matching operation (R7.7a) inside a transaction, so the operation's own checks and the database constraints (R7.3) are checked too. The transaction commits only if everything passes. |
| R7.7a | **Files are applied through operations (issue #5, D40):** each changed file becomes one operation call through `invoke()`, as the owner on the `yaml-sync` channel, whoever started the sync (bootstrap, the watcher, a command). So a file edit gets the same permission check, validation and audit entry as the same change made with the CLI or API. `user.yaml` → `settings_replace`, `organizations.yaml` → `registry_replace`, `base.yaml` → `library_replace`, a profile → `profile_write`, `application.yaml` → `application_replace`, `posting.md` → `posting_replace`, a fit report, cover letter or answer → `document_propose` (as the owner's own edit: honesty findings are warnings and the document goes back to draft). The replace operations archive what's left out (R7.10) and refuse a change to recorded events (R6.4). Other changes an operation causes, such as an approved letter flagged out of date after a profile edit (R9.14), are passed on only after the file's transaction commits, and dropped on a dry run. |
| R7.8 | **Unit of sync:** each file is applied as a whole or not at all, in dependency order (`user.yaml`, `organizations.yaml`, `base.yaml`, profiles, then jobs). If a file fails, the database keeps that file's last good state and other valid files still sync. A file that depends on a failed file's new content waits until the failure is fixed. |
| R7.9 | **Error reporting:** every problem names the file, line and field, says what's wrong in plain words, and shows a valid example (R10.16). Problems appear in the CLI output, in `SYNC-STATUS.md` in the user's folder, and as a banner on the tracker website. A failed sync never stops the app; it keeps serving the last good data. |

#### Deletions and history

| Id | Requirement |
| --- | --- |
| R7.10 | Removing an entry from a YAML file archives it in the database; it does not delete it. Archived content can't be selected for new documents but stays in existing snapshots and history. Permanent deletion is a separate, confirmed command and is audited. Deleting a job folder is reported, not applied; `archive-job` and `delete-job` do that explicitly. |

#### Changes made by the app

| Id | Requirement |
| --- | --- |
| R7.11 | **No silent overwrites:** sync applies only the changes the user made in a file since its last sync (three-way merge). If the user changed a field in YAML that the app also changed in the database since then, sync stops for that file and reports the conflict with both values. The user resolves it by editing the file. |
| R7.12 | **Write-back:** when data changes through the API (the agent drafting a profile, `new-job`, a CLI command, any other interface), the app updates the matching YAML or Markdown file right away. It keeps comments, key order and formatting (R10.15). The files therefore always show the current data, so the user edits from an up-to-date copy. Write-back updates the stored last-synced hash so the change isn't read back in as a user edit. |
| R7.13 | Before any sync or migration that changes data, the app backs up the database automatically to `/backups/<user>/` (section 11.2). |

#### Recovery

| Id | Requirement |
| --- | --- |
| R7.14 | If the database is lost, `restore` brings back a backup, and the next sync re-applies any newer edits from the YAML files. `import --fresh` builds a new database from the YAML files alone. Snapshots, the audit log and archived items can't be rebuilt that way, which is why backups are automatic. |

### 8.8 Tracker website (local; read-only unless the owner signs in)

| Id | Requirement |
| --- | --- |
| R8.1 | Runs on localhost only and is never deployed publicly. |
| R8.2 | **Applications list:** filter and sort by stage, company, department, date applied, next action, stale flag. The company and department filters are pick-lists from the organization registry. |
| R8.3 | **Application detail:** posting summary, contacts, and a timeline of every event. Shows the resume, letter and answer versions sent, each with a button that rebuilds the PDF from its snapshot and downloads it. Also a list of every snapshot for the job, a text diff between any two, and a view of the profile's differences from the public profile. |
| R8.4 | **Dashboard:** applications per week, response rate, median days to first response, conversion between stages, outcomes by source, response rate with and without a cover letter, and the bullets and tags that appear most often in resumes that reached interviews. |
| R8.5 | **Upcoming actions:** next actions sorted by due date; stale applications. |
| R8.6 | **Company view:** every application to one company (and its departments) over time, so the user can see past contact before applying again. |
| R8.7 | **Sync status:** time of the last sync, files waiting on a fix, conflicts and how to resolve them. |
| R8.8 | **Owner sign-in (D32):** without signing in, the site is read-only. The owner can sign in with an owner token or a one-time link from `rb web-login` (valid 10 minutes, once); agent tokens can't. Signed in, jobs, events and changes to job details the agent proposed get **Confirm** and **Discard**, which run the same operations as `rb confirm-job`, `rb discard-job`, `rb confirm-event`, `rb discard-event`, `rb confirm-update` and `rb discard-update`, and the job page gets an **Edit details** form that runs the same operation as `rb set` (R6.6), all as the owner on channel `web`, checked and audited like any other call. No other writes happen on the website. |
| R8.9 | **Session and forged requests:** the session cookie is `HttpOnly`, `SameSite=Strict`, scoped to the user's pages and signed with a per-install secret kept outside the data folder; only a hash of the session id is stored. Sessions end after 12 hours idle, 7 days in all, or on sign-out. Every action is a `POST` with a CSRF token tied to the session, from the site's own origin and a local host name (against DNS rebinding); a `GET` never changes anything. Pages can't be framed, and the Content-Security-Policy allows no inline or third-party scripts: only the site's own script files (`script-src 'self'`), which today are the help pop-up (R8.11) and the Copy buttons (R8.12, R8.13). **Every** request to the website and HTTP API, reads included, must name an allowed host (`localhost`, `127.0.0.1`, `::1`, plus `RB_WEB_HOSTS`; an embedding host passes its own list) or is refused before any route runs, so a page that rebinds its own name to 127.0.0.1 can't read the user's data through their browser. |
| R8.10 | **AI agent status:** the home page and `rb status` say whether an AI agent is set up: connected (an assistant reached the app directly, through MCP or the HTTP API), an agent token issued but not used yet, used only through copy and paste (`rb propose`; no assistant has connected directly), or not set up, with the setup steps. They show when the agent was last active on each channel, the MCP client's name if it sends one, its last few actions with links to the jobs they touched, what's waiting for review, and a warning when the assistant keeps using an API token that was replaced. The app can't see the assistant's own settings, so this is built only from what reaches it: every operation the agent runs (reads included, at most one note a minute), MCP connections (audited at most once an hour) and the agent token. It never shows a token. |
| R8.11 | **Online help:** every page has a **Help** link that opens the user guides (quick start, user manual, working with an AI agent) in one pop-up help window; clicking a help link again brings that window to the front and shows the requested page there. Pages link to the relevant help section where it helps (sync status, AI agent card, proposed jobs, sign-in). Help is rendered on the server from the same Markdown files as `docs/`, packaged with the app so it works offline and matches the running version, with raw HTML escaped; links between guides stay inside help, links to contributor docs go to the repository, marked as external. A simple search covers headings and text. Without scripts or with pop-ups blocked, help opens in a single reused tab. |
| R8.12 | **Copy-ready commands:** a **Commands** page lists the everyday `rb` commands by task, filled in with the chosen job's id and its latest rendered resume's snapshot id; only values the app can't know stay as placeholders. Each job's page starts with **Next steps**: only the steps that fit the job's state (posting, interest notes, tailored profile, draft items it uses, letters and answers to approve, rendering, recording the application, then replies, interviews and follow-ups, proposed events, closed or archived), each with the file to edit or the commands to run. The steps come from a read operation, `job_next_steps`, which `rb next <job-id>` prints too. Commands are kept as argument lists and a test parses each with the real `rb` parser; a **Copy** button (a site script, R8.9) copies one; commands that pipe input show a macOS/Linux and a Windows PowerShell form; `--user` appears only when it's needed. Suggestions only: nothing changes until the user runs a command (D34). |
| R8.13 | **Ready-made prompts:** one catalog of prompts for the user's AI assistant, one for each thing the agent may do: tailor a resume, redo the drafts after answering questions, a cover letter, the "why here" answer, a message to the hiring team, add a job, record a recruiter's email, turn notes into bullets, review bullets, update the public profile. Each has a wording for a connected assistant (naming the MCP tools) and, where it can work without tools, one for copy and paste (with the `rb brief` to run first and the `rb propose` commands after); otherwise the `rb` command to use instead. They're shown on a **Prompts** page (filled in for the chosen job; the wording follows the AI agent status and can be switched), in a job's Next steps, as MCP prompts (`prompts/list`, `prompts/get`) and by `rb prompts`. Prompts only ask for what the agent's permissions allow, never for invented content, estimated metrics, a stage, a confirmation or an approval; a test checks that every tool a prompt names is one the agent may use. The service checks are unchanged, so no wording gets around them (D36). |

### 8.9 Cover letters and application answers

For any job with a tailored profile, the user can ask for a cover letter, a short answer to "Why do you want to work here?", or both. Each one makes the case for fit using **the tailored resume for that job**, not the whole content library.

#### Where content may come from

| Id | Requirement |
| --- | --- |
| R9.1 | Evidence about the candidate comes only from the confirmed bullets and summary phrases selected in that job's profile: under `roles`, `projects` or `earlier_roles` (R3.4). If a point isn't on the tailored resume, it isn't in the letter or answer. |
| R9.2 | Facts about the employer and role come only from the posting and the user's own notes in the application (`interest_notes`: why they want this job, what they know about the company). The AI does not add company facts from anywhere else. |
| R9.3 | The candidate's motivation ("why I want this") comes only from `interest_notes`. If the notes are empty, the AI asks the user and does not make up enthusiasm. A draft can still be produced, with a marked placeholder where the motivation goes. |

#### Short answer: "Why do you want to work here?"

| Id | Requirement |
| --- | --- |
| R9.4 | Brief: 100 words or fewer by default (`answer_max_words` in `user.yaml`), plain prose, no headings or bullet points. |
| R9.5 | Shape: one sentence on why this role and employer (from R9.2 and R9.3), then one or two sentences linking the posting's top requirements to the strongest evidence on the tailored resume, with a metric where one exists. |
| R9.6 | Stored as an application answer with the question text, so other common questions ("Tell us about a project you're proud of", "Why are you a good fit?") can be added later with no change to the model. Each answer has a slug (`why-here` by default). A short **message to the hiring team**, for forms that ask for one instead of a cover letter, is an answer with slug `hiring-team` and the question "Message to the hiring team"; the answer rules (R9.1–R9.4, R9.9–R9.14) apply to it as written. |
| R9.6a | An answer exports as plain text or PDF, named `<Name>_Answer_<slug>_<job-id>.<txt\|pdf>`, so a job's answers never overwrite each other. The slug is recorded in the answer's snapshot; snapshots saved before slugs were recorded keep the old name, `<Name>_Answer_<job-id>`. |

#### Cover letter

| Id | Requirement |
| --- | --- |
| R9.7 | 250–350 words by default (`cover_letter_max_words` in `user.yaml`), three or four short paragraphs: why this role, two or three pieces of evidence mapped to the posting's main requirements, a short close. |
| R9.8 | Rendered to PDF with the same template and contact details (from secrets) as the tailored resume, as `<Name>_Cover_Letter_<job-id>.pdf`, always rebuilt from its snapshot (section 8.12). |

#### Honesty checks (both document types)

| Id | Requirement |
| --- | --- |
| R9.9 | Every sentence that makes a claim about the candidate cites the bullet ids it rests on, with a marker at the end of the sentence: `[cite: bullet-id, other-id]`. Citations are stored with the draft and stripped when rendering. |
| R9.10 | The validator fails a draft when a cited id isn't in the job's profile or isn't confirmed; when a number, tool or technology in the text doesn't appear in a cited bullet; when a claim sentence has no citation; or when the text is over its word limit. |
| R9.11 | The writing style rules apply (no "proven record", "passionate about", "leverage" and the like). Phrases on a banned list in `user.yaml` fail validation. |

#### Review and tracking

| Id | Requirement |
| --- | --- |
| R9.12 | The agent can only create drafts. The owner approves (or edits and approves) before a letter or answer can be marked as sent. Owner edits made in the working-copy file sync back like any other input. If they add text that doesn't trace back to a bullet, the validator shows warnings but does not block, and the warnings are recorded on the version. |
| R9.13 | Every approved letter and answer gets a version id, which is its snapshot id (section 8.12). The application and its `applied` event record which versions were sent, next to the resume version id. |
| R9.14 | If the tailored profile changes after a letter or answer was approved, that letter or answer is flagged as out of date. |

### 8.10 First run, discovery and seeded files

The app sets up and checks user folders by itself. Seeded files explain themselves, so a user can fill them in without reading separate documentation.

#### Bootstrap script

| Id | Requirement |
| --- | --- |
| R10.1 | A `bootstrap` script runs every time the server container starts, before the server starts (DK14). One-off CLI containers skip the full bootstrap and run only migrations and the quick sync in R7.5. It can also be run by hand (`bootstrap`, or `doctor` for a check-only mode that changes nothing). |
| R10.2 | It checks that `/data`, `/db`, `/output` and `/backups` exist and are writable. If one isn't, it stops with a plain message saying which mount is wrong and how to fix it. |
| R10.3 | **Discovery:** every folder directly under `/data` that contains a `user.yaml` is a user space. Folders starting with `.` or `_` are ignored. A folder without `user.yaml`, or with a name that isn't a valid slug (lowercase letters, digits, hyphens), is reported, not changed. |
| R10.4 | **First run:** if no user space exists, the script creates one named by `RB_DEFAULT_USER` (set in `.env`; the seeded `.env.example` sets it). If that variable isn't set, it creates nothing and prints the exact command to run. |
| R10.5 | **Repair:** for each user space found, the script creates any missing folders (`profiles/`, `jobs/`) and seed files, the user's database (with migrations applied), and the matching `/output/<user>/` and `/backups/<user>/` folders. |
| R10.6 | **Never destructive:** the script never overwrites, moves or deletes an existing file. Running it again changes nothing (it is idempotent). |
| R10.7 | **Sync and report:** it then runs a sync (R7.5) and ends with a short summary per user: created, already present, sync result, problems found (validation errors, conflicts, example entries still in place, missing contact secrets, snapshot limits nearly reached, a newer seed template version available). The same summary appears in `SYNC-STATUS.md` and on the tracker website's home page. |
| R10.8 | It goes through the service API like every other interface (A1), as a `system` actor whose only permissions are `space:init`, `space:check` and `space:sync`. Everything it creates is recorded in the audit log. |

#### Commands for adding things

| Id | Requirement |
| --- | --- |
| R10.9 | `init-user <name>` creates a new user space from the seed. `new-job` asks for the company and department, offering existing registry entries first (section 8.11). It then builds the job id (R1.3), creates the application in the database and writes a job folder with a seeded `posting.md` and `application.yaml`, already set to stage `interested` with today's date. `new-job --company <name> --department <name>` skips the questions. |
| R10.9a | `delete-user <name>` deletes one user space: its data folder, database and exports, through the sync adapter, `Storage` and `FileStore` (A1). It needs permission `space:delete`, which only the owner holds; the agent can't, and the website has no way to. Nothing is deleted unless the name is typed back exactly, and the default user (`RB_DEFAULT_USER`) is refused, since bootstrap would recreate it empty. The deletion is audited, then a final backup is taken and the user's backups are kept, unless the user asks for them to be deleted too. Restoring that backup and recreating the files brings the user back (D35). |

#### Seeded files that explain themselves

| Id | Requirement |
| --- | --- |
| R10.10 | Every seeded YAML file starts with a short header comment: what the file is for, what to do first, and what happens if a field is left empty. |
| R10.11 | Every field has a comment on its line or just above it, giving its meaning, allowed values (for example every lifecycle stage) and a short example. Optional fields are present with `null` or an empty list, not left out, so the user can see everything that's possible. |
| R10.12 | **Smart defaults** fill in everything that has a sensible default:<br>• `user.yaml`: stale threshold 14 days, answer limit 100 words, cover letter limit 350 words, default template, a starter list of banned phrases, publishing turned off, snapshot limits (R12.9), backup retention (R7.13), standard secret names (`RB_CONTACT_EMAIL`, `RB_CONTACT_PHONE`), time zone from the container.<br>• `base.yaml`: the full skeleton of every section, with one worked example in each (a summary phrase, a role with two bullets, a project, a skill group), using `example-` ids.<br>• `organizations.yaml`: one example company with one department, showing the name, slug, aliases and notes fields.<br>• `profiles/public.yaml`: selects the examples, so the very first `render --preview` produces a working sample resume (R2.3).<br>• Job `application.yaml`: stage, today's date, the event log with its first event, and every optional field from section 8.6.<br>• Job `posting.md`: headings for the posting text, the source URL and the user's notes, each with a one-line hint. |
| R10.13 | Example entries are `status: draft`, so they can never appear in a real resume. The validator and the bootstrap report list any `example-` entries still in place until the user replaces or deletes them. |
| R10.14 | **Editor help:** each seeded YAML file begins with a `yaml-language-server` schema line that points to JSON Schemas generated from the domain models (A3), which bootstrap writes to `/data/<user>/.schemas/`. Schemas are per user because they list that user's registry slugs (R11.3). Editors such as VS Code then offer autocomplete, allowed values and error highlighting as the user types, catching most problems before sync does. |
| R10.15 | **Comments survive app writes:** write-back (R7.12) keeps comments, key order and formatting when it updates a YAML file, so the explanations are never lost. |
| R10.16 | **Error messages explain the fix:** validation errors name the file, line and field, say what's wrong in plain words, and give an example of a valid value. |
| R10.17 | Each user space gets a `START-HERE.md`: a checklist of five or fewer steps (fill in `user.yaml`, replace the examples in `base.yaml`, add a job, render, open the tracker). It is a checklist, not a manual. |
| R10.18 | Seed templates live in `src/resume_builder/templates/seed/` in the code repo and carry a `schema_version`. The fictional `examples/sample-user/` is built from the same seed, filled with fictional content, so the seed is always tested. |

### 8.11 Organization registry (companies and departments)

Companies and departments are recorded once and reused. This keeps job ids consistent and gives every company field a pick-list.

| Id | Requirement |
| --- | --- |
| R11.1 | Each user has an organization registry. Each company has a display name, slug, aliases (other spellings seen in postings, such as "ACME" or "Acme Corp."), optional website and notes, and a list of departments. Each department has the same fields. |
| R11.2 | The registry lives in the database (`companies` and `departments` tables). `organizations.yaml` is its input file and is kept current by write-back, so entries added through `new-job` appear there too. All access goes through the service API (A1). |
| R11.3 | **Recall as a field selector:** wherever a company or department is entered, the app offers existing entries from the database first:<br>• CLI (`new-job`): a numbered pick-list, matched against names and aliases as the user types, with "add new" as the last option.<br>• YAML editing: the generated JSON Schema (R10.14) lists the registry slugs as allowed values for `company` and `department`, so the editor offers them in autocomplete. The schema is regenerated whenever the registry changes.<br>• Website filters: drop-down lists from the registry. |
| R11.4 | **No accidental duplicates:** a new name that closely matches an existing name or alias (ignoring case, punctuation and suffixes such as Inc, LLC or Corp) triggers "Did you mean …?" before a new entry is created. |
| R11.5 | A new company or department can be created on the fly while adding a job. It is added to the registry right away. |
| R11.6 | Renaming a company or department updates its display name everywhere. Existing job ids keep their original slug (R1.3), and the old name is kept as an alias. |
| R11.7 | Merging two entries (for example, duplicates found later) moves every application to the surviving entry, keeps the other name as an alias, and is recorded in the audit log. |
| R11.8 | The agent may suggest a company and department from a pasted posting, offering an existing registry entry first. The user confirms the choice before the job is created. |

### 8.12 Document snapshots and exact regeneration

Every document the app produces can be rebuilt later exactly as it was, even after the content library, the profile or the templates have changed. The app keeps the text needed to rebuild a document, never the PDF or HTML itself.

#### What a snapshot is

| Id | Requirement |
| --- | --- |
| R12.1 | Every time the app produces a document, it first saves a **snapshot**: the complete, resolved input needed to render it. This covers resumes, cover letters, application answers, fit reports, profiles and the public site. |
| R12.2 | A snapshot contains:<br>• the final text of every section, in render order: name, summary, every role and bullet as it read at that moment, skills, education, letter paragraphs, answer text<br>• the source references: bullet ids, content hashes of the profile and content library, and the organization entries used<br>• the template id and exact template version<br>• render settings: page size, fonts, and the ATS or styled variant<br>• the app version and Docker image digest<br>• the time it was created, the actor that created it and the reason (draft, approved, sent, public publish) |
| R12.3 | A snapshot never contains contact details or secrets. It records which secret names were used and a one-way fingerprint of their values. Regeneration uses the current values. If the fingerprint doesn't match, the user is told that the contact details have changed since the original. |
| R12.4 | Snapshots are stored in the database as canonical JSON (sorted keys, fixed formatting). The snapshot id is the hash of that content, so identical inputs give the same id and are stored once. Database backups include them. |
| R12.5 | A snapshot is never edited after it is written. Approving or sending a document records that against the snapshot id; it does not change the snapshot. |

#### Exact regeneration

| Id | Requirement |
| --- | --- |
| R12.6 | `regenerate <snapshot-id>`, the website, and the API rebuild a document from its snapshot alone, without reading the current content library, profile or registry. The result is returned to the user as a download or written to `/output` for export. It is never kept by the app. |
| R12.7 | **"Exactly the same" means:**<br>• The text and layout are always identical: every released template version stays in the image unchanged, and the snapshot names the version to use.<br>• With the same image version, the PDF is byte-for-byte identical. PDF metadata such as the creation date comes from the snapshot, not the current time, and rendering avoids anything random. A test checks this on every build.<br>• With a newer image, the text and template are still identical, but font or renderer updates could shift layout slightly. The app says so before rebuilding, and names the image version that produced the original. |
| R12.8 | **Moment in time:** the user can ask for a document "as of" a date. The app picks the latest snapshot of that document created at or before that time. |

#### Storage limit

| Id | Requirement |
| --- | --- |
| R12.9 | Limits are set in `user.yaml`, with seeded defaults: at most 10 snapshots per document type per job (`snapshots.max_per_job_and_type`) and at most 1,000 per user (`snapshots.max_total`). |
| R12.10 | Snapshots referenced by a sent application, the current public site, or a user's manual pin are **protected**. They are never deleted automatically and don't count toward the per-job limit. |
| R12.11 | When a limit is reached, the oldest unprotected snapshot of that job and type is deleted (for the total limit, the oldest unprotected snapshot overall). The deletion is recorded in the audit log, and bootstrap reports how close each user is to the limit. |
| R12.12 | If the total limit is reached and every snapshot is protected, the new document is not created. The user gets a clear message to raise the limit or unpin something. The app never silently fails to keep a snapshot of a document it produced. |
| R12.13 | The user can pin, unpin and delete snapshots by hand. Deleting a protected snapshot requires an explicit confirmation and is audited. |

#### Rendered files are not stored

| Id | Requirement |
| --- | --- |
| R12.14 | PDFs and HTML are rendered on demand from snapshots. `/output` is a disposable export folder: the app never reads from it, never relies on it, and offers a `clean-output` command. Deleting it loses nothing. |
| R12.15 | Documents shown in the tracker website are rendered on the fly from snapshots and streamed to the browser, without writing files. |

### 8.13 Job intake (from a URL or pasted text)

#### Problem

Starting a job today means running `new-job`, then copying the posting into `posting.md` and typing a dozen fields into `application.yaml` (title, location, salary, date posted, requisition id, and so on). That transcription is tedious, error-prone, and the step where users give up. The AI agent can't help, because it may not create jobs or edit the user's files. Intake turns one input, a link or a block of text, into a fully populated job that the user reviews once.

#### Goals

- One input starts a job: the posting URL by default, or pasted text when the URL can't be fetched.
- The posting is kept exactly as published, because postings disappear once a job closes and the fit report and letters depend on it.
- Every field the posting states is filled in. Nothing the posting doesn't state is guessed.
- The user reviews and confirms once, before anything is created.
- The same operation works for the CLI, the website, the HTTP API and the agent, with the agent limited to proposing.

#### Non-goals

- Crawling, searching or monitoring job sites. Intake fetches only the one URL the user gives it, when the user asks.
- Logging in to job sites or using the user's browser session or cookies.
- Running JavaScript in a headless browser (see D26).
- Rewording, summarizing or "cleaning up" the posting text.
- The agent filling in `interest_notes`, `interest_level` or anything else that is the user's own judgment. It may only pass on what the user said, word for word, for the user to check (R13.22a).

#### User flow

```text
rb new-job <url>                       # default: fetch the page
rb new-job --file intake/<name>.txt    # fallback: text the user saved in their intake folder
rb new-job --stdin [<url>] < posting.txt  # fallback: piped text
rb new-job                             # no posting: create the job only (R10.9), as before
        │
        ▼
capture  →  extract fields  →  match company/department  →  check for duplicates  →  preview
        │
        ▼
user confirms (or edits a field, or picks another company)  →  job created, posting.md and application.yaml written
```

#### Input sources

| Id | Requirement |
| --- | --- |
| R13.1 | **URL is the default input.** On the CLI, intake is `new-job` given a posting: `new-job <url>` (or `--url <url>`) fetches the page and uses it as the posting. The URL is stored as `posting_url`. Without a link, `--stdin` or `--file`, `new-job` creates the job only, as in R10.9 (D30). |
| R13.2 | **Pasted text is the fallback**, for pages that need a login (for example LinkedIn), render only with JavaScript, block automated requests, or for postings received by email. The user saves the text to a file in their intake folder (`/data/<user>/intake/`) or pipes it on stdin. A URL found in the text is used as `posting_url`; otherwise the user may pass `--url`. |
| R13.3 | When a fetch fails or returns too little posting text (for example an empty JavaScript shell or a login page), intake stops, says why in plain words, and shows the exact paste-fallback command. It never creates a job from a page it couldn't read. |
| R13.4 | The intake folder is seeded by bootstrap with a short `README.md` explaining its use. Files in it are read only by the sync adapter (A1), only when the user runs intake on them, and never deleted or moved by the app. After a successful intake, `SYNC-STATUS.md` notes that the file has been taken in and can be deleted. |

#### Fetching

| Id | Requirement |
| --- | --- |
| R13.5 | A fetch is one HTTP(S) GET of the given URL, started only by an explicit intake request (or a resume import from a link, R2.11). No crawling, no follow-up requests for other pages, no retries in a loop. |
| R13.6 | Fetches send no cookies, credentials or user data, and use a generic user agent that names the app but not the user. |
| R13.7 | **Safe fetching:** only `http` and `https`; the resolved address of the host and of every redirect is checked, and loopback, private, link-local and other internal addresses are refused; at most 5 redirects; a timeout (default 20 s) and a size limit (default 2 MB); only HTML or plain-text responses are accepted (Markdown, `text/markdown`, counts as plain text), except that a site adapter may ask for a JSON API response (R13.16, D31), which is then the only type accepted for that request and is refused if nested too deeply or invalid. |
| R13.8 | Fetched content is untrusted data. It is never executed or rendered as HTML in the website, and text passed to the agent is marked as posting content, not instructions. |
| R13.9 | Fetching can be turned off per user (`intake.fetch: false` in `user.yaml`), in which case intake accepts pasted text only. Timeout and size limit are also set there, with seeded defaults. |

#### Capturing the posting

| Id | Requirement |
| --- | --- |
| R13.10 | The **captured posting** is stored in the database with the source (URL or file), its content type (HTML, plain text, or JSON from a site adapter), the time it was captured, and a content hash. It is never edited afterwards. Re-capturing the same job's posting adds a new capture; the latest one is current. |
| R13.11 | `posting.md` is written from the capture: the page's main content converted mechanically to Markdown (headings, lists, paragraphs), with navigation, footers, scripts and cookie banners removed. The wording is never changed. A "Source" section records the URL and capture date, and the existing "My notes" section is kept for the user. |
| R13.12 | Once written, `posting.md` follows the normal sync rules (section 8.7): the user's edits sync back, and intake never overwrites a `posting.md` the user has changed. |

#### Extracting fields

| Id | Requirement |
| --- | --- |
| R13.13 | Intake fills the posting fields of section 8.6: company and department (as names, matched in R13.17), title, posting URL, source, external posting id, location, work arrangement, salary range as posted, date posted and closing date. |
| R13.14 | **Structured data first.** When the page carries schema.org `JobPosting` data (common on employer and job-board sites), fields are taken from it: `title`, `hiringOrganization`, `jobLocation`, `jobLocationType`, `baseSalary`, `datePosted`, `validThrough`, `identifier`. Otherwise, rules in code recognize common patterns: salary ranges, "Posted on …" dates, "Job ID / Req #" labels, and the source from the URL's domain (`linkedin` for LinkedIn, `job_board` for known boards, otherwise `company_site`). |
| R13.15 | **Never guess.** A field the posting does not state stays `null`; `work_arrangement` stays `unknown` unless the posting says remote, hybrid or onsite. Every extracted value records the passage of the capture it came from, and the service rejects any value whose passage is not in the capture. This check is in code (section 6.2), so it applies to any agent. |
| R13.16 | **Site adapters** for sites with a known page or API format (for example applicant tracking systems that many employers share) are an extension point (A4, [ARCHITECTURE.md](ARCHITECTURE.md)): a plugin can register an adapter for a domain that returns the capture and fields. Core ships the generic HTML and structured-data path; adapters must follow R13.5–R13.8. An adapter may read a site's JSON API (D31); it must then supply the posting as Markdown, and each block of it (heading, paragraph, list item) must keep, word for word and in order, the wording of one string value in the JSON. Blocks may come in any order. Markdown that fails is refused; there is no generic JSON conversion to fall back on. |

#### Company, department and duplicates

| Id | Requirement |
| --- | --- |
| R13.17 | The company and department names found in the posting are matched against the registry (names and aliases, R11.3–R11.4). The preview offers the best existing match first. A new entry is created only when the user chooses it at confirmation (R11.5). |
| R13.18 | Before creating a job, intake checks for an existing application with the same posting URL or the same company and external id. If one exists, the preview says so and offers to re-capture the posting for that job instead of creating a new one. |

#### Review and confirmation

| Id | Requirement |
| --- | --- |
| R13.19 | Intake shows a **preview** before creating anything: each field with its value and source passage, the matched company and department, the job id it would create (R1.3), and any duplicate. On the CLI the user confirms, edits a field, or picks another registry entry; `--yes` accepts the preview as shown. `new-job`'s own `--title` and `--source` count as the user's values. |
| R13.20 | On confirmation, intake creates the application exactly as `new-job` does (R10.9: stage `interested`, today's date), with the extracted fields filled in, then writes `posting.md` and `application.yaml` through write-back. The whole intake is applied in one transaction: all or nothing. |
| R13.21 | After intake, the CLI prints the next steps: add `interest_notes` (with `rb set`) if they weren't given, then ask the agent for a profile and fit report (or run `brief`). The owner can give their own answers during intake too: `--set interest_notes=...`, `interest_level`, `next_action`, `next_action_due` and `notes`, or by typing the field's name at the review prompt. |

#### The AI agent

| Id | Requirement |
| --- | --- |
| R13.22 | The agent may **propose** an intake (`job_intake_propose`, permission `application:propose`): a URL or text, plus optional field values with their source passages. The proposal is a draft that does not create a job, a registry entry or any file. It is subject to the same checks as R13.15 and R13.18. |
| R13.22a | **The user's answers (D37):** a proposal may also carry `from_user`: interest level, `interest_notes`, source, next action and its due date, and notes, as the user told them to the agent, word for word. They are not checked against the posting. The agent asks for them and never writes or guesses them. `rb confirm-job`, the website and `job_intake_proposals` show them as "from you, via the agent", and the owner can change or clear each one before confirming. Nothing is saved until the owner confirms. |
| R13.23 | The owner sees pending intake proposals in the CLI, the website and `SYNC-STATUS.md`, and confirms or discards each one. Confirmation follows R13.19–R13.20. |
| R13.24 | The agent may trigger a fetch by proposing an intake with a URL (D27). The fetch follows R13.5–R13.8, and nothing is created until the owner confirms. |

#### Operations and interfaces

| Id | Requirement |
| --- | --- |
| R13.25 | Intake is defined once as service operations (A5) and appears in the CLI, HTTP API and MCP together: `job_intake_preview` (owner, `application:write`: captures and extracts; it stores the capture, unattached, so confirming never fetches again, and creates nothing else), `job_intake` (owner, `application:write`), `job_intake_propose` (agent, `application:propose`), `job_intake_confirm` and `job_intake_discard` (owner, `application:write`). `job_intake` and `job_intake_confirm` take the owner's `answers` (R13.22a). Each declares its permission and has permission tests (B9). |
| R13.26 | `new-job` without a posting keeps working unchanged for users who prefer to type the fields. |
| R13.27 | Each fetch is recorded in the audit log with the URL's host (not the full URL or the content), the result and the actor. |

#### Privacy

| Id | Requirement |
| --- | --- |
| R13.28 | Captured postings are private data, like `posting.md` (section 10). They stay in the user's database and backups and never appear in logs, commit messages or file names. The intake folder is inside the user's data folder and is gitignored with it. |
| R13.29 | The only outbound traffic intake adds is the single GET of R13.5 to the URL the user supplied. The tests and the example user never make real network requests; fetching is tested against a local stub. |

#### Acceptance

- `new-job <url>` on a page with schema.org `JobPosting` data produces a preview with every stated field filled and nothing invented; confirming creates the job, `posting.md` and `application.yaml` in one step.
- `new-job --file` or `--stdin` on text pasted from a login-only site produces the same result, minus anything the text doesn't state.
- A JavaScript-only page, a login page, a private address, an oversize response and a redirect loop each stop with a clear message and create nothing.
- An agent proposal with a value not found in the capture is rejected; a valid one waits for the owner and creates nothing until confirmed.
- Running intake twice on the same URL offers a re-capture instead of a duplicate job.

## 9. Application architecture

### 9.1 Layers: one API in front of all data

```text
Interfaces    CLI · tracker website · HTTP API · MCP server (stdio, HTTP) · bootstrap
              (the AI agent uses MCP, the HTTP API or the CLI as the `agent` actor)
                  │   call service operations only
Service API   ContentService · ProfileService · LetterService · RenderService · ApplicationService
              ReportService · SnapshotService · SyncService
                  │   every call: request context → authorize → validate → act → audit
Storage API   Repositories: Content · Organization · Profile · Application · Event · Document · Snapshot · Audit · SyncState
              PostingCapture · IntakeProposal · ApplicationDraft · EventDraft · Token · WebSession
                  │
Backend       SQLite (source of truth)

Sync adapter  YAML/Markdown files in /data  ⇄  SyncService
              (the only code that reads or writes user files: import with validation, write-back with comments kept)
```

| Id | Requirement |
| --- | --- |
| A1 | All reads and writes of user data go through the service API. Interface code (CLI, website, scripts, GitHub workflows) never opens files under `data/`, parses YAML or Markdown, or runs SQL. |
| A2 | SQL exists only in the SQLite repositories, always as parameterized queries. YAML and Markdown are read and written only in the sync adapter. Services never touch either directly. |
| A3 | Services work with typed domain models (`Bullet`, `Profile`, `Application`, `Event`, `Contact`, `Organization`, `Document`, `Snapshot`, …). The same models generate the JSON Schemas for YAML validation and editor help (R10.14), so the file format and the database can't drift apart. |
| A4 | Each repository is an interface. SQLite is the only backend in this repo. Another database can be added as a new backend without changing services or interfaces (see ARCHITECTURE.md). |
| A5 | **Generated API.** Each service operation is defined once, with typed inputs and outputs. The HTTP API and its OpenAPI spec are generated from those definitions. The CLI and website are thin clients of the same operations. Nothing can be done in only one interface. |
| A6 | Validation runs in the service layer on every write. YAML input goes through the extra safety stages in R7.7 first, then through an operation like any API write (R7.7a): the same permission check, validation and audit. |
| A7 | A user editing YAML is an input to the app, not a change to stored data. It takes effect only when the sync service validates and applies it, recorded in the audit log as the owner's change through the YAML channel. |
| A8 | Once the API exists, the AI agent creates profiles, fit reports, draft bullets and draft events by calling the API (CLI or HTTP), not by writing files. |

### 9.2 Ready for RBAC without a refactor

The app does not build user accounts. It does build the structure that role-based access control needs, so adding roles later only means adding configuration and a new policy.

| Id | Requirement |
| --- | --- |
| B1 | Every service call takes a **request context**: actor id, the actor's roles, the user space it acts in and a request id. No service method works without one. |
| B2 | Every operation declares the permission it needs, for example `content:read`, `content:propose`, `content:confirm`, `settings:write`, `profile:write`, `resume:render`, `letter:propose`, `letter:approve`, `snapshot:read`, `snapshot:regenerate`, `snapshot:pin`, `snapshot:delete`, `organization:read`, `organization:write`, `organization:merge`, `application:read`, `application:propose`, `application:write`, `event:append`, `site:publish`, `space:init`, `space:check`, `space:sync`, `data:backup`, `data:restore`. The declaration is part of the operation's definition, so the generated API documents it as well. |
| B3 | Authorization is checked in one place: the service layer calls a policy interface before every operation. Each app instance has its own policy, so two instances in one process never share one. The app ships one policy: the local **owner** has every permission in their own user space and none outside it. Two non-human actors also exist: `agent` (B7) and `system` (the bootstrap script, limited to `space:init`, `space:check` and `space:sync`). A sync the system starts applies each file as the owner, since the files are the owner's own input (R7.7a). |
| B4 | Interfaces never make permission decisions; they only supply identity. No code path skips the check, including internal calls between services. |
| B5 | Every stored record belongs to a user space and carries `created_by`, `created_at`, `updated_by` and `updated_at`. Every event records its actor. Changes from YAML sync are recorded as the owner's, with the channel noted. |
| B6 | Every write creates an audit entry (actor, channel, operation, target, time, result) in the audit log table. |
| B7 | **The AI agent is its own actor** with the `agent` role. It can read content and postings, propose draft bullets, import a resume the user gave it, as text or a link, as draft library entries (R2.6, R2.11), write profiles and fit reports, draft cover letters and answers, draft events and job intakes (R13.22), and propose changes to an application's own fields in the user's words (R6.7). It can read the organization registry and suggest entries (R11.8), but cannot add, rename or merge them. It can read and regenerate snapshots, but cannot pin or delete them. It cannot confirm content, approve letters or answers, confirm events, publish, sync, back up or restore. This makes the honesty rule ("AI advises, the user decides") something the code enforces, not just a convention. |
| B8 | Roles and their permissions are configuration, not code. Besides `owner`, `agent`, `system` and `viewer` (read only), a host can add roles such as `reviewer` (for example a career coach who can read and comment but not confirm) or `admin`. The request context also carries a tenant id, and the policy refuses any access across tenants. |
| B9a | **Who is calling:** every HTTP API call carries a local bearer token that identifies the actor. `rb token --actor agent` (or `--actor owner`) issues a token, stores only its hash, and replaces the previous one; the user gives the agent token to their AI tool. The MCP endpoint (`/mcp/<user>`) takes only the agent's token. Requests without a valid token are read-only and limited to the website's pages, unless the owner has signed in to the website (R8.8, R8.9), which gives a session for its Confirm and Discard buttons only. The CLI acts as the owner unless `RB_ACTOR_TOKEN` names another actor. These checks prevent mistakes and keep the agent within its role; they are not protection against someone who already controls the user's machine or Docker. |
| B9 | Tests check that every operation declares a permission, a deny-all policy blocks every operation, and a read-only policy blocks every write. |

The interfaces that implement A1–A5 and B1–B8 (app factory, storage, request context with tenant, input adapter, secrets, file stores, template overrides and operation plugins) are described for contributors in [ARCHITECTURE.md](ARCHITECTURE.md).

## 10. Privacy and security

- Job postings, profiles, fit reports, application records, contacts, databases, backups and tailored PDFs never leave the user's private storage.
- The only thing published is the user's public resume HTML, rendered from the public profile, and only when the user sets a publish target.
- Commit messages, file names and workflow logs that could ever be public must not contain employer names, posting text or contact details.
- Contact data about third parties (recruiters, interviewers) is personal data. It is kept only locally, and the user can delete it.
- The website and HTTP API answer only to local host names (R8.9), on every request, against DNS rebinding: binding to 127.0.0.1 alone doesn't stop a page in the user's own browser from reaching it.
- Signing in to the website (R8.8, R8.9) doesn't change who can reach it. The session cookie never leaves the user's machine, only its hash is stored, and every website action needs a CSRF token, the site's own origin and a local host name.
- The tracker website and HTTP API are reachable only from the user's own machine. Inside the container the server listens on all interfaces, as containers require, but `compose.yaml` publishes the port only on the host's `127.0.0.1`.
- Snapshots never contain contact details or secrets (R12.3).
- Job intake (section 8.13) is the only feature that makes outbound requests: one GET of a URL the user supplies, with no cookies or user data, and internal addresses refused (R13.5–R13.8).
- YAML input is read with a safe loader and strict limits (R7.7), so a malformed or hostile file can't run code or exhaust memory.
- User data, databases, backups, rendered output and secrets never go into the Docker image. `.dockerignore` excludes them, and a CI check fails the image build if any of them appear in the build context.
- Every write is recorded in the audit log (section 9.2), so it is always clear who or what changed a record: the user (through YAML or a command) or the AI agent.

## 11. Distribution and deployment: Docker

The application ships and runs as a Docker image. The image contains only code, templates, migrations and the system libraries it needs. All user data stays on the user's machine and is mounted into the container.

### 11.1 Image

| Id | Requirement |
| --- | --- |
| DK1 | One image runs the HTTP API, the tracker website, the sync watcher and the CLI. The same image is used in development, CI and by end users. |
| DK2 | The base image is pinned to an exact version. The image includes WeasyPrint's system libraries (Pango and similar) and a fixed set of fonts, so PDFs render the same on every machine. |
| DK3 | The container runs as a non-root user. `PUID` and `PGID` build arguments or environment variables match the host user, so files written to bind mounts belong to that user. |
| DK4 | The image has a health check that calls an unauthenticated health operation in the API. |
| DK5 | Images are versioned (semantic version tags) and can be published to a container registry (D11), so new users never need to build the image themselves. |
| DK14 | The container entrypoint runs `bootstrap` (R10.1), including migrations and the first sync, before starting the server. If bootstrap finds a broken mount, the container stops with a clear message instead of starting half-working. Sync validation errors do not stop the container (R7.9). |

### 11.2 Volumes

| Mount      | Type                     | Holds                                                                    | Who writes it                                          | Back up                                                     |
| ---------- | ------------------------ | ------------------------------------------------------------------------ | ------------------------------------------------------ | ----------------------------------------------------------- |
| `/data`    | Bind mount (host folder) | User input and working copies: YAML and Markdown files, `SYNC-STATUS.md` | The user, and the app's write-back (R7.12)             | Recommended: make each user folder its own private git repo |
| `/db`      | Named volume (`rb-db`)   | `app.db` for each user: the source of truth                              | Only the SQLite repositories                           | Required. Automatic, to `/backups` (DK6)                    |
| `/output`  | Bind mount (host folder) | Temporary PDF/HTML exports                                               | Only the render service, when the user asks for a file | No. Disposable; everything can be rebuilt from snapshots    |
| `/backups` | Bind mount (host folder) | Timestamped database backups                                             | Only the backup service                                | This is the backup. Copy it off the machine                 |

| Id | Requirement |
| --- | --- |
| DK6 | The database lives on a named volume, not a bind mount, so SQLite file locking works reliably on every host OS. Backups use SQLite's online backup, so they are consistent while the app runs. They are taken automatically before any sync or migration that changes data (R7.13) and once a day. Retention is counted separately for each kind, so frequent pre-change backups can't push out the daily ones: the last 30 pre-change backups and the last 30 daily backups (`backups.keep` in `user.yaml`). `backup` and `restore` commands are available at any time. |
| DK7 | Because `/data` is a plain folder on the host, the user can edit YAML with any editor and keep it in git outside the container. The sync watcher picks up changes within seconds (R7.5). |
| DK8 | Removing or rebuilding the container never loses data: everything persistent is on a mount or the named volume. Losing `/output` loses nothing either (R12.14). |

### 11.3 Configuration and secrets

| Id | Requirement |
| --- | --- |
| DK9 | All settings come from environment variables with documented defaults: data roots (R1.7), port, default user, log level, sync polling interval. |
| DK10 | Contact details and publish credentials come from Docker secrets (`/run/secrets/...`) or environment variables, using the names in `user.yaml`. They are never stored in an image, a mount, the database or a log. |
| DK11 | A `compose.yaml` in the code repo defines the service, the four mounts, the localhost-only port and the secrets. `docker compose up` is the standard way to run the app. |
| DK11a | **One-step setup:** `setup.sh` (macOS, Linux, Git Bash) and `setup.ps1` / `setup.bat` (Windows) create `.env` from `.env.example` on the first run (with `PUID`/`PGID` filled in on macOS and Linux) and stop so the user can fill it in; run again, they build and start the app and install the `rb` command. Running them again, for example after an update, is safe. |

### 11.4 Interfaces from outside the container

| Id | Requirement |
| --- | --- |
| DK12 | The CLI runs through the same container (`docker compose run --rm app <command>` or `docker compose exec app <command>`), so it goes through the same service API (A1). |
| DK12a | **The `rb` command:** `scripts/install-rb.sh` (macOS, Linux, Git Bash) and `scripts/install-rb.ps1` / `install-rb.cmd` (Windows) install a small `rb` wrapper on the user's `PATH` that runs `docker compose run --rm app rb <arguments>` for this folder's `compose.yaml` and `.env`, from any folder. It is only a shortcut for DK12: every command still runs in the container. `--uninstall` removes it. |
| DK13 | The AI agent runs on the host, as the `agent` actor (B7), and reaches the app only through the channels in section 6.1: the MCP server over stdio (`rb mcp`, in the container), MCP over HTTP (`/mcp/<user>`) or the HTTP API on `127.0.0.1` with an agent token, or the containerized CLI (`rb brief`, `rb propose`). It never edits the mounted files directly once the API exists (A8). |

### 11.5 Code and data repos

- The code repo can be made public. It holds no user data (`data/`, `output/` and `backups/` are gitignored, R1.5).
- Each user's `/data/<user>/` folder can be its own private git repo. Because of write-back (R7.12), its history shows both the user's edits and the app's changes.
- Development and tests may run outside Docker with the same environment variables pointing at `examples/sample-user/` and a temporary database, but Docker is the supported way to run the app.

## 12. Decisions

All decisions were approved on November 13, 2026. D2 was revised the same day at the product owner's direction, and D1 uses the format the product owner specified. D15 and D16 were still marked open when the decisions were completed; they take the recommended choice and can be changed later through `user.yaml` (D15) or a small code change (D16). D23 (license) was decided on November 14, 2026. D24–D36 (job intake, section 8.13, website sign-in, MCP over HTTP, copy-ready commands, deleting a user and ready-made prompts) were decided on November 15, 2026, and D37 (your answers on a job) the same day. D38 (importing an existing resume) and D39 (reading Word and PDF resumes) were decided on November 16, 2026.

| #   | Decision                                                                     | Choice                                                                                                                                                                                 |
| --- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | Job id format                                                                | `YYYY-MM-DD-<company>[-<department>]` built from the entry date and organization registry slugs (R1.3, section 8.11)                                                                   |
| D2  | When SQLite becomes the source of truth                                      | **From the start.** YAML is the user's input; validated input is synced into SQLite, which is the source of truth from then on (section 8.7)                                                  |
| D3  | Database storage: named volume or bind mount?                                | Named volume `rb-db` (DK6), with `backup` and `restore` to get the file out                                                                                                            |
| D4  | Base image                                                                   | `python:3.12-slim`, pinned by digest, with WeasyPrint system libraries and a fixed font set                                                                                            |
| D5  | Web and API stack                                                            | Python with FastAPI and Pydantic: typed operations generate the HTTP API and OpenAPI spec (A5). Website uses server-rendered pages calling the same operations; no frontend build step |
| D6  | Are the lifecycle stages and fields in section 8.6 right?                    | As written in section 8.6                                                                                                                                                              |
| D7  | Stale threshold                                                              | 14 days                                                                                                                                                                                |
| D8  | Permission names and the `agent` role's permissions (B2, B7)                 | As written in B2, B3 and B7                                                                                                                                                            |
| D9  | Word limits for the short answer and cover letter (R9.4, R9.7)               | 100 words; 250–350 words                                                                                                                                                               |
| D10 | Do owner edits that add claims without citations block or only warn? (R9.12) | Warn and record. The owner is the author, but the warning keeps it visible                                                                                                             |
| D11 | Container registry for published images                                      | GitHub Container Registry, next to the code repo                                                                                                                                       |
| D12 | Default host paths for the mounted folders                                   | `./data`, `./output` and `./backups` next to `compose.yaml`, all changeable in a `.env` file                                                                                           |
| D13 | YAML library (must keep comments, R10.15)                                    | `ruamel.yaml` in round-trip mode                                                                                                                                                       |
| D14 | What first run does when `RB_DEFAULT_USER` is not set (R10.4)                | Create nothing; print the exact `init-user` command. The seeded `.env.example` sets it, so most users never see this                                                                   |
| D15 | Snapshot limits (R12.9)                                                      | 10 per document type per job; 1,000 per user; protected snapshots exempt from the per-job limit                                                                                        |
| D16 | Contact details in snapshots (R12.3)                                         | Not stored. Fingerprint only; regeneration uses current values and warns if they changed                                                                                               |
| D17 | Where snapshots live (R12.4)                                                 | In the database, following D2. Covered by the automatic backups                                                                                                                        |
| D18 | Backups location                                                             | Separate `/backups` mount (default `./backups`)                                                                                                                                        |
| D19 | Write app changes back to the YAML files? (R7.12)                            | Yes                                                                                                                                                                                    |
| D20 | Unit of sync: per file, or all changed files together? (R7.8)                | Per file, in dependency order, so one typo doesn't block every other change                                                                                                            |
| D21 | When sync runs (R7.5)                                                        | At startup, on file change (watcher with polling fallback), before CLI commands, and on demand                                                                                         |
| D22 | Backup retention (DK6)                                                       | Before every data-changing sync or migration, plus daily; keep the last 30 of each kind (DK6)                                                                                          |
| D23 | License | AGPL-3.0-or-later (decided November 14, 2026). Contributions need a DCO sign-off and are also licensed under MIT, so the project can later move to MIT or Apache-2.0 (`CONTRIBUTING.md`) |
| D24 | Job intake (section 8.13): approve the feature as written? | Approved as written (November 15, 2026) |
| D25 | Paste fallback location (R13.2, R13.4) | Use an `intake/` folder in the user's data folder plus stdin; intake runs only on command, never automatically when a file appears |
| D26 | JavaScript-rendered pages (R13.3) | No headless browser in the image (size, attack surface); use the paste fallback or a site adapter (R13.16) |
| D27 | May the agent trigger a fetch (R13.24)? |  Yes, through `job_intake_propose` with a URL, since the fetch is one GET under R13.5–R13.8 and creates nothing until the owner confirms |
| D28 | New permission `application:propose` for the agent (R13.22) | Add it to B2 and the agent role in B7 |
| D29 | Keep every capture of a posting, or only the latest (R13.10)? | Keep all; they are small text and show when a posting changed |
| D30 | One command or two for starting a job (R13.1)? | One: `new-job`. Given a link, `--stdin` or `--file` it runs intake; without, it creates the job only. A separate `intake` command was confusing next to `new-job` (decided November 15, 2026) |
| D31 | May site adapters read JSON APIs (R13.7, R13.16)? | Yes, only when an adapter asks for JSON. Captures may then be `application/json` (migration 0004). The wording check for JSON is per block: each Markdown block must match one JSON string value, words in order; blocks may come in any order (decided November 15, 2026) |
| D32 | May the website confirm or discard what the agent proposed (R8.8, R8.9)? | Yes, after the owner signs in with an owner token or a one-time `rb web-login` link. Cookie session (`HttpOnly`, `SameSite=Strict`, signed, 12 hours idle, 7 days at most), synchronizer CSRF tokens, `Origin`/`Host` checks, no scripts; nothing else on the website writes (decided November 15, 2026) |
| D33 | May assistants connect to the MCP tools with a URL (issue #35)? | Yes, for assistants on the user's computer: MCP's Streamable HTTP transport at `/mcp/<user>` on the tracker, with the agent's token, plain JSON answers, no event stream or sessions, and Origin and Host checks. The stdio command stays the default (it works without the tracker running). Never reachable from the internet (decided November 15, 2026) |
| D34 | Should the website show the commands to run, or run them (issue #36)? | Show them, filled in and ready to copy, with per-job next steps from a read operation. Running commands from the website would make it a second way to change data beside the signed-in actions of D32; copying keeps every change on the command line, where the user sees it (decided November 15, 2026) |
| D35 | How is a user deleted? | `rb delete-user <name>`, owner only, after typing the name back; never the default user. It keeps the user's backups by default and adds a final one, so a deletion can be undone; `--delete-backups` removes those too. Not on the website or for the agent (decided November 15, 2026) |
| D36 | Should the app suggest what to ask the AI assistant (issue #37)? | Yes: a fixed, built-in set covering what the agent may do, in both wordings, on the website, in MCP and in the CLI. Users can't change the wording yet; overrides in `user.yaml` can come later if needed (decided November 15, 2026) |
| D37 | May the agent pass on the user's interest notes and other answers (issue #38)? | Yes, as the user's words for the user to check: `from_user` in a job proposal (R13.22a) and drafted changes on an existing job (R6.7). The agent never writes or guesses them; nothing is saved until the owner confirms. The owner can also change these fields through an operation (`application_update`, `rb set`, the website), not only in `application.yaml` |
| D38 | How does a new user start `base.yaml` from an existing resume (issue #1)? | `rb import-resume` reads HTML, Markdown or plain text from the intake folder, stdin or a link (fetched like a job posting, D27) with plain parsing, no built-in AI, and adds everything as drafts with new ids; existing entries are never changed and likely duplicates are reported. Permission `content:propose`, so an agent can import text the user gave it, as drafts only. Skill groups get a `status` (missing means `confirmed`) so imported skills need confirming too (decided November 16, 2026) |
| D39 | Which converters read Word and PDF resumes (issue #39)? | `mammoth` for Word and `pdfminer.six` for PDF, called directly. These are the converters Microsoft's MarkItDown uses inside; MarkItDown itself (0.1.9 with its docx and pdf extras) added about 200 MB to the image, mostly ONNX Runtime and NumPy for a file-type detector it always imports, against about 26 MB for the two converters, with the same text. Word goes through the import's own HTML reader (decided November 16, 2026) |
| D40 | How does YAML sync write to the database (issue #5)? | Through operations, like every other client: each changed file is one `invoke()` call as the owner on `yaml-sync` (R7.7a), with new whole-item operations `settings_replace` (new owner-only permission `settings:write`), `registry_replace`, `library_replace` (`content:confirm`, since a library carries statuses), `application_replace` and `posting_replace`, plus the existing `profile_write` and `document_propose`. The adapter keeps the safe reading, line-numbered problems, three-way merge, backup and dry run; the operation's transaction nests inside the adapter's as a savepoint (decided November 16, 2026) |

## 13. Milestones

1. Core skeleton: domain models, request context, policy interface with owner, agent and system policies, audit log, repository interfaces, SQLite backend with migrations and constraints, generated HTTP API, CLI. Includes the RBAC tests (B9). Dockerfile, `compose.yaml` and `.dockerignore` with the four mounts. CI builds the image and runs the tests inside it.
2. YAML input: schemas, seed templates with comments and smart defaults, generated JSON Schemas, the sync service with every validation stage (R7.7), change detection, conflict detection, write-back, automatic backups, `SYNC-STATUS.md`, `bootstrap`, `doctor`, `init-user` and `new-job`, and the fictional example user built from the seed. Includes tests with malformed and hostile YAML.
3. Rendering service and versioned templates, snapshots with limits and protection, and exact regeneration with a byte-for-byte test, all against the example user.
4. Tailoring end to end through the API with the example user: posting, profile, fit report, PDF, then the "Why do you want to work here?" answer and cover letter with honesty checks.
5. Application service and the tracker website, including sync status (owner sign-in for Confirm and Discard came later, R8.8).
6. Optional publishing of the exported public site to the target set in `user.yaml`.
7. First real user onboarded with `init-user` and running a live application from start to finish. Anything they need that's missing becomes a generic feature (R1.6).
8. Job intake (section 8.13, D24–D29): capture from a URL and from pasted text, structured-data and rule-based extraction with passage checks, registry matching, duplicate check, preview and confirm, agent proposals, and fetch-safety tests against a local stub.
9. Working with the agent day to day (D30–D37): owner sign-in on the website with Confirm and Discard (R8.8, R8.9), the Host check on every request, AI agent status (R8.10), online help (R8.11), copy-ready commands (R8.12), ready-made prompts in MCP, the website and the CLI (R8.13), MCP over HTTP (D33), site adapters for JSON APIs (D31), `delete-user` (R10.9a), and the user's own answers on a job, passed on by the agent and editable through `rb set` and the website (R6.6, R6.7, R13.22a). One-step setup scripts and the `rb` command (DK11a, DK12a).
10. Importing an existing resume (R2.5–R2.12, D38, D39): `import-resume` for HTML, Markdown and plain text, from a file or a link, drafts only, duplicate and registry checks, and a status on skill groups.
