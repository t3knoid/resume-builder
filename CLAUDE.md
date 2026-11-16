# CLAUDE.md

resume-builder: a generic product for honest, tailored resumes, cover letters and application answers, plus a job-application tracker. Anyone can use it by adding their own content folder. The product owner is onboarded as the first user, the same way as anyone else.

- `docs/PRD.md` is the product spec. `docs/QUICKSTART.md` and `docs/USER-MANUAL.md` are the user docs: update them in the same change as any user-visible behavior.
- **Keep the PRD and ARCHITECTURE in sync with the code, in the same change.** When you add, change or remove a feature (including a bug fix that changes behavior, a new command, operation, permission, channel, page or file name):
  - `docs/PRD.md`: add, change or remove the requirement (R-id) and, for a product choice, the decision (D-id, in number order, with its date). Update every place that describes it (scope in 6.1, the 9.1 diagram, milestones, DK requirements), not just one table. Never reuse or renumber ids; mark a removed requirement as removed rather than deleting its id.
  - `docs/ARCHITECTURE.md`, when the change touches the structure: layers, `invoke()`, request context and channels, the policy, `Storage` methods and their guarantees, `App` options, plugin extension points, interfaces or transports.
  - Then the user docs as above. Before finishing, search the docs for the old name or behavior so nothing stale is left.
- Code lives in `src/resume_builder/`: `models.py` (domain models, also the YAML schemas), `security.py` (context, permissions, policy), `operations.py` (registry + `invoke`), `plugins.py` (extension points), `storage.py` (the `Storage` interface) with `db.py` + `repositories.py` (SQLite, the only SQL), `filestore.py`, `secretstore.py`, `changes.py`, `yamlio.py` + `sync.py` + `sync_ops.py` (the YAML/Markdown input adapter, the only user-file access), `services/` (operations), `api.py` + `web/app.py` (`create_app`: generated HTTP API + website), `mcp.py` (MCP server for agents), `cli.py` (`rb`), `server.py`. `docs/ARCHITECTURE.md` documents the extension points. Agent integrations are documented per assistant in `docs/AI-AGENTS.md`; the agent rules text lives once, in `services/profiles.py` (`AGENT_RULES`).
- Run tests: `.venv/bin/python -m pytest -q` locally (PDF tests skip without WeasyPrint), and the full suite in the image: `docker build -t resume-builder:ci . && docker run --rm --entrypoint sh resume-builder:ci -c 'cd /app && python -m pytest -q'`.

## Data flow: YAML in, SQLite as the record (PRD section 8.7)

- Users enter data by editing YAML and Markdown files in `/data/<user>/`. Those files are **input**.
- The sync service validates changed files and applies them to the user's SQLite database, `/db/<user>/app.db`. From then on the **database is the source of truth**. Every service, the renderer and the website read only from the database.
- When the app changes data (API, CLI, agent), it **writes back** to the matching YAML/Markdown file, keeping comments, so the files always show current data.
- Sync applies only what the user changed since the last sync (three-way merge). If the user and the app changed the same field, stop and report the conflict; never silently overwrite either side.
- Validation stages before anything reaches the database: safe reading (UTF-8, size limit, safe loader, no duplicate keys, depth and alias limits), schema, content rules, references, history rules, merge check, then a database dry run in a transaction. A file is applied whole or not at all.
- A failed file keeps its last good state in the database. Report problems in the CLI, `SYNC-STATUS.md` and the website. A sync failure never stops the app.
- Removing something from YAML archives it; it never hard-deletes. Changing or removing an already-synced event is rejected.
- Back up the database automatically before any data-changing sync or migration.

## Structure: users are separated

All content specific to one user lives in that user's data folder and database. Nothing else is about a specific user.

```text
Dockerfile, compose.yaml, .dockerignore
docs/                     # PRD.md, user guide
src/                      # shared code: no user-specific content
src/resume_builder/templates/   # resume/letter templates and seed templates (packaged)
src/resume_builder/migrations/  # versioned SQLite schema migrations (packaged)
examples/sample-user/     # fictional user for tests and docs
tests/
data/, output/, backups/  # default host folders for mounts (gitignored)
```

The app runs in Docker with four mounts (PRD section 11):

```text
/data/<user>/             # bind mount: input files and write-back working copies
  START-HERE.md, SYNC-STATUS.md
  intake/                 # pasted posting text for `rb new-job --file`; read only on command, never changed by the app
  user.yaml               # settings: name, publish target, secret names, template, limits
  base.yaml               # content library input: id, text, tags, metric, evidence, limits, status
  organizations.yaml      # companies and departments (pick-list source)
  profiles/public.yaml
  jobs/<job-id>/
    posting.md, application.yaml
    profile.yaml, fit-report.md, cover-letter.md, answers/why-here.md   # working copies of app drafts
/db/<user>/app.db         # named volume rb-db: SQLite, the source of truth (incl. snapshots, audit log, sync state)
/output/<user>/           # bind mount: disposable PDF/HTML exports, never storage
/backups/<user>/          # bind mount: database backups
```

- Roots come from `RB_DATA_ROOT`, `RB_DB_ROOT`, `RB_OUTPUT_ROOT` and `RB_BACKUP_ROOT` (container defaults `/data`, `/db`, `/output`, `/backups`). Never hard-code paths.
- Only text goes in `/data`. Never put user data, databases, backups, output or secrets into the image. Keep `.dockerignore` in sync.
- The container runs as a non-root user matched to the host (`PUID`/`PGID`). The server listens on all interfaces inside the container; `compose.yaml` publishes the port only on `127.0.0.1`.
- Run the CLI through the container (`docker compose run --rm app <command>`). Tests run in the image in CI.
- Every command takes a user (`--user <name>`) and touches only that user's folder and database.
- Code, templates, tests and docs never contain a real user's name, employers, contact details or job data. Anything that varies between users goes in `user.yaml`.
- Tests, fixtures and doc examples use only the fictional `examples/sample-user/`, never real data.
- No special cases for any user, including the first one. Turn a user's need into a generic feature configured in `user.yaml`, or keep it in their data.
- Context or notes about one specific user belong in that user's data folder, not in `docs/` or this file.
- Ids (bullets, jobs) only need to be unique within one user.
- Job ids are `YYYY-MM-DD-<company>[-<department>]`: the date the job was first entered, plus the company and department slugs from the organization registry (PRD section 8.11). `new-job` builds the id; never type one by hand. Ids never change after creation and contain employer names, so treat them like employer names under the privacy rules.
- Always pick a company or department from the registry when one matches (check names and aliases). Add a new entry only when none matches, and suggest it to the user rather than creating it silently.

## Database rules

- SQL only in the SQLite repositories, always parameterized.
- Enforce integrity in the schema too: foreign keys on, `CHECK` constraints for allowed values, `UNIQUE` ids per user, `NOT NULL` where required. WAL mode.
- Every schema change is a new migration in `src/resume_builder/migrations/`. Migrations run at startup after a backup and never lose data.

## Bootstrap and seeded files (see PRD section 8.10)

- `bootstrap` runs on every container start: it checks the mounts, finds user spaces (folders under `/data` with a `user.yaml`), creates a first user from `RB_DEFAULT_USER` if none exist, adds anything missing, applies migrations and runs a sync. It never overwrites, moves or deletes a file, and running it twice changes nothing.
- Seed templates live in `src/resume_builder/templates/seed/`. Whenever you add or change a field in a domain model, update the seed in the same change: a one-line comment with meaning, allowed values and an example; a smart default where one makes sense; the field present (as `null` or empty) even when optional. Regenerate the JSON Schemas.
- Example seed entries use `example-` ids and `status: draft`, so they can never be rendered as real content.
- Write-back must keep comments, key order and formatting. Never write YAML in a way that strips comments.
- Validation errors must name the file, line and field, explain the problem plainly and show a valid example.
- `examples/sample-user/` is built from the same seed, so seed changes are covered by tests.

## Snapshots and regeneration (see PRD section 8.12)

- Every generated document (resume, cover letter, answer, fit report, profile, public site) is saved first as a snapshot in the database: canonical JSON with the fully resolved text, template id and version, render settings, source hashes, app version and image digest. Render only from a snapshot.
- Never store PDFs or HTML. `/output` is a disposable export folder; the app never reads from it.
- Snapshots are never edited. They never contain contact details or secrets, only secret names and a one-way fingerprint of their values.
- Regeneration uses the snapshot alone, never current content. With the same image the PDF must be byte-for-byte identical: take PDF metadata dates from the snapshot and avoid anything random. Keep the byte-for-byte test passing.
- Released template versions never change. Changing a template means adding a new version.
- Respect the snapshot limits in `user.yaml`. Never automatically delete a protected snapshot (sent, public, or pinned). Every deletion is audited.

## Architecture rules (see PRD section 9)

- Keep the app embeddable and multi-tenant-ready (PRD section 6.2): never assume one user per process, never read user data outside a request context, never add a file-only feature without an operation behind it, and keep honesty checks in code rather than prompts.
- Business plans, pricing and plans for hosted products are private and never go in this repo, which may become public (PRD 11.5).

- **Extend through the seams, never around them** (`docs/ARCHITECTURE.md`): operations and permissions via the registry or a plugin, data via `Storage`, exports and backups via `FileStore`, secrets via `SecretStore`, user files only in the sync adapter (services call `space.changed(...)`). Build apps with `build_app()` / `create_app()`. Nothing in this repo may import, reference or document code from outside it.
- **All data access goes through the service API.** CLI, website, scripts and workflows call service operations. They never open files under `data/`, parse YAML or Markdown, or run SQL.
- YAML and Markdown are read and written only by the sync adapter. Services use typed domain models, never file paths or rows.
- The same domain models generate the JSON Schemas used for YAML validation and editor help, so the file format and the database can't drift apart.
- Each service operation is defined once with typed inputs and outputs. The HTTP API and OpenAPI spec are generated from those definitions. Do not add an operation to only one interface.
- **Check every code change for two things, in the same change:**
  - **An operation (and so the HTTP API):** anything a user or agent can do or see (a CLI command, a website page or action, an MCP handler, data read from a file or a module) goes through a registered operation. If an interface reads a module, a file or storage directly, add the operation and have every interface call it. Verify with the OpenAPI spec, which lists every operation.
  - **An extension point for a host that embeds the app** (for example a hosted, multi-tenant service with its own accounts, storage and no user files): if a host would have to patch core to change the new behavior (who the caller is, which users exist, where data or files live, which formats or sites are read, what happens around an operation), add a generic seam instead: an `App` option with today's behavior as the default, or an `ext.add_...` plugin hook, documented in `docs/ARCHITECTURE.md` with a test. Never a hosted-only flag or special case, and never a reference to any product built on this repo.
  - If neither applies, say so in your summary of the change.
- **Built ready for RBAC:** every service call takes a request context (actor, roles, user space, request id). Every operation declares a permission. Authorization happens in one policy check in the service layer, with no bypasses. Interfaces never make permission decisions.
- The tracker website is read-only unless the owner signs in (PRD R8.8, R8.9). Any website action is a `POST` that goes through the checks in `web/session.py` (session, CSRF token, same origin, local host) and then `invoke` as the owner on channel `web`. Never add a write to the website any other way, and never one that changes state on a `GET`.
- Every record carries `created_by/at` and `updated_by/at`; every write creates an audit entry (including the channel: YAML sync, CLI, API).
- The AI agent is its own actor with the `agent` role: it may propose drafts, profiles, fit reports, cover letters, application answers, draft events and new jobs (`job_intake_propose`), and pass on the user's own answers (interest notes and the like) word for word (`from_user`, `application_propose_update`), but never write those answers itself, never confirm content, events or jobs, approve letters or answers, publish, sync, back up or restore. Once the API exists, use it (CLI or HTTP) to create these instead of editing files.
- New code needs tests for its permission declaration. Keep the RBAC tests passing (deny-all blocks everything; read-only blocks writes).

## Honesty rules (non-negotiable)

- Never invent experience, tools, metrics, dates, titles or outcomes. If it is not in the user's content library, it does not go in a resume.
- Profiles reference bullet ids and summary phrase ids only. Do not write new resume text in a profile.
- Only `status: confirmed` bullets may be rendered. New or changed content goes in as `status: draft` and the user confirms it.
- Respect each bullet's `limits` field. Do not stretch a single experience into broad expertise.
- Never estimate a metric. If `metric` is null, leave it null and list it in the fit report as a question for the user.
- When a posting asks for something not in the content library, list it under `gaps` and ask whether it is a real gap or missing information. Do not fill it.

## Cover letter and application answer rules (see PRD section 8.9)

- Build them from **that job's tailored profile** only: the confirmed bullets and summary phrases it selects. If a point isn't on the tailored resume, leave it out.
- Facts about the employer come only from the posting and the user's `interest_notes`. Motivation ("why I want this") comes only from `interest_notes`. If the notes are empty, ask the user. Never make up enthusiasm or company facts.
- "Why do you want to work here?": 100 words or fewer by default, plain prose. One sentence on why this role and employer, then one or two sentences linking the posting's top requirements to the strongest evidence, with a metric where one exists.
- Cover letter: 250–350 words by default, three or four short paragraphs.
- Every claim sentence cites the bullet ids it rests on. Every number and tool mentioned must appear in a cited bullet.
- Citation format: `[cite: bullet-id, other-id]` at the end of the sentence. Use `[MOTIVATION NEEDED]` when `interest_notes` is empty.
- These are drafts only. The agent cannot approve them; the user approves each one before it is sent.

## Tracker rules

- Applications and their events live in the database. `application.yaml` is the input and write-back copy.
- The event log is append-only. Correct a mistake by adding a new event that refers to the old one, not by rewriting history.
- Never make up events, dates, contacts or outcomes. You may draft an event from text the user pastes (for example a recruiter email), clearly marked for the user to confirm.
- Never change an application's stage on your own. Flag stale applications; the user decides.

## Privacy rules

- Postings, profiles, fit reports, application records, contacts, databases, backups and tailored PDFs stay in private storage. Never copy them to a public repo or site.
- The only published output is the user's public resume HTML, rendered from the public profile and sent to the publish target in `user.yaml`, if one is set.
- Never put employer names, posting text or contact details in commit messages, file names or workflow logs that could reach a public location.
- Contact details come only from the secrets or environment variables named in `user.yaml`. Never commit or store them, including in the database.
- The tracker website and API are reachable only on the host's `127.0.0.1` (port published that way in `compose.yaml`) and are never deployed publicly.
- Contact details and publish credentials reach the container as Docker secrets or environment variables, never as files in a mount or the image.

## Writing style (content library and fit reports)

- Lead each bullet with the outcome, then the evidence.
- Plain, specific language. Avoid corporate filler such as "proven record", "leveraged", "synergy", "in collaboration with cross-functional teams".
- Use the real names of tools and systems.
- Keep the user's voice. When unsure, ask rather than polish.

## Fit report format (`fit-report.md`)

1. Strong matches: posting requirement, then the bullet ids that support it.
2. Weak spots: partial matches, with what is missing.
3. Gaps: requirements with no support in the content library.
4. Questions for the user: missing metrics, unclear claims.
5. Notes on the role: what the posting suggests about the actual work, office arrangement and seniority.

## Workflow per application

(`rb` = `docker compose run --rm app rb`.)

1. `rb new-job` starts every job (picks or adds company/department, builds the job id, creates the application at stage `interested` and the job folder). What else it does depends on what it's given:
   - **The posting's link:** `rb new-job <url>`. Also fetches the posting once, previews the fields read from it, and writes them to `application.yaml` and the posting to `posting.md` after one confirmation.
   - **The site needs a sign-in:** save the posting text in the user's intake folder, then `rb new-job --file intake/<name>.txt <url>` (or pipe it: `rb new-job --stdin <url> < posting.txt`). Same as above, from the pasted text.
   - **No link at all:** `rb new-job`. Seeds `posting.md` and `application.yaml`; the user pastes the posting into `posting.md` and sync picks it up.
2. As the agent: use the resume-builder MCP tools if connected (`agent_brief`, then `profile_write` and `document_propose` with kind `fit_report`). Otherwise `rb brief <job-id>` and submit drafts with `rb propose <job-id> profile|fit_report|...` (stdin). Never edit files in the user's data folder directly: that bypasses the agent's checks.
3. Fix any validation failures reported by sync or the API.
4. `rb render <job-id>` (add `--preview` while drafts remain, `--ats` for the plain variant). The snapshot id is the resume version id.
5. If requested, `document_propose` a cover letter (`kind: cover_letter`) or answer (`kind: answer`, `slug: why-here`); every claim cited.
6. The user reviews (edits to the `.md` working copies sync back) and runs `rb approve <job-id> cover_letter|answer`.
7. After submitting: `rb event <job-id> --stage applied --documents <snapshot ids>`. Later events are appended as they happen; the agent may only `event_propose`.
8. Everything is visible at http://localhost:8000.
