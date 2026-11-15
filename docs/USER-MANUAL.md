# resume-builder user manual

resume-builder keeps one complete, honest record of your career and builds a tailored resume, cover letter or short application answer for each job from it, without inventing anything. It also tracks every application from first interest to final outcome, and remembers exactly which documents you sent.

New here? Start with the [quick start](QUICKSTART.md). Connecting an AI assistant: [AI-AGENTS.md](AI-AGENTS.md). This manual explains everything in detail.

## Contents

1. [How it works](#1-how-it-works)
2. [Installing and running](#2-installing-and-running)
3. [Your folder](#3-your-folder)
4. [How your edits reach the app (sync)](#4-how-your-edits-reach-the-app-sync)
5. [Your content library: base.yaml](#5-your-content-library-baseyaml)
6. [Companies and departments](#6-companies-and-departments)
7. [Tracking a job](#7-tracking-a-job)
8. [Tailoring a resume](#8-tailoring-a-resume)
9. [Cover letters and application answers](#9-cover-letters-and-application-answers)
10. [Rendering, snapshots and exact copies](#10-rendering-snapshots-and-exact-copies)
11. [Recording what you sent](#11-recording-what-you-sent)
12. [The tracker website](#12-the-tracker-website)
13. [Your public resume](#13-your-public-resume)
14. [Backups and restoring](#14-backups-and-restoring)
15. [Working with an AI agent](#15-working-with-an-ai-agent)
16. [Command reference](#16-command-reference)
17. [Privacy and security](#17-privacy-and-security)
18. [Troubleshooting](#18-troubleshooting)
19. [What isn&#39;t built yet](#19-what-isnt-built-yet)

---

## 1. How it works

- **You write; the app keeps the record.** You enter information by editing YAML and Markdown files in your folder. The app checks every change and stores it in a database (SQLite). From then on, the database is the official record. When the app itself changes something (for example when you log an event from the command line), it writes the change back into your files, so they always show the current data.
- **One content library, many tailored documents.** `base.yaml` holds every claim about your experience. A tailored resume is a *profile*: a list of ids from `base.yaml` in the order you want, with no new text. So nothing can appear on a resume that isn't in your library.
- **Drafts versus confirmed.** Each claim is `draft` until you've checked it, then `confirmed`. Only confirmed content goes into real documents.
- **AI advises, you decide.** An AI agent can propose profiles, fit reports, letters and answers, but only as drafts. It can't confirm content, approve a letter, or send anything.
- **Every document can be rebuilt exactly.** Each time the app produces a document, it saves a *snapshot*: the exact text and design version it used. PDFs aren't stored; they're rebuilt from snapshots whenever you ask, identical to the original.

## 2. Installing and running

### Requirements

- Docker Desktop (macOS, Windows) or Docker Engine with the Compose plugin (Linux).
- A text editor. VS Code with the Red Hat YAML extension gives autocomplete and live checking.

### Settings (.env)

Copy `.env.example` to `.env` and edit it. `.env` is never committed.

| Setting              | Meaning                                                                            | Default       |
| -------------------- | ---------------------------------------------------------------------------------- | ------------- |
| `RB_DEFAULT_USER`  | Your folder name, created on first start. Lowercase letters, digits, hyphens.      | none          |
| `RB_CONTACT_EMAIL` | Your email for documents.                                                          | none          |
| `RB_CONTACT_PHONE` | Your phone for documents. Leave empty to omit it.                                  | none          |
| `PUID`, `PGID`   | Your user and group id (`id -u`, `id -g`), so files the app writes stay yours. On Windows, keep the defaults. | 1000 / 1000   |
| `TZ`               | Your time zone.                                                                    | UTC           |
| `RB_HOST_DATA`     | Where your folder lives on your computer.                                          | `./data`    |
| `RB_HOST_OUTPUT`   | Where exported PDFs go.                                                            | `./output`  |
| `RB_HOST_BACKUPS`  | Where database backups go.                                                         | `./backups` |
| `RB_HOST_PORT`     | Port for the website on your computer.                                             | 8000          |
| `RB_WEB_HOSTS`     | Extra names the website answers to, comma-separated, for example a name you gave this computer. Only names that point to this computer. | none (`localhost`, `127.0.0.1` and `::1` always work) |

The names `RB_CONTACT_EMAIL` and `RB_CONTACT_PHONE` are set in your `user.yaml` (`contact.email_secret`, `contact.phone_secret`). You can instead use Docker secrets with those names (files in `/run/secrets/`). Secret names may contain only letters, digits, `_`, `.` and `-`.

### Start, stop, update

```sh
docker compose up -d --build     # start (and rebuild after an update)
docker compose logs -f app       # see what it's doing, including sync results
docker compose down              # stop; nothing is lost
```

To update: `git pull`, then `docker compose up -d --build`. Database changes are applied automatically on start, after a backup.

The setup script does the first start and the `rb` install in one go: `sh setup.sh` on macOS and Linux, `setup.bat` on Windows (double-click it, or type `.\setup.bat` in PowerShell: PowerShell needs the `.\`, and a bare `setup` runs an unrelated command). It creates `.env` from `.env.example` if there isn't one yet and stops so you can fill it in, refuses to start while `RB_DEFAULT_USER` is still `your-name`, then runs `docker compose up -d --build` and the `rb` installer below. Running it again is safe, so you can also use it to update after `git pull`.

### The `rb` command

Every command runs inside the container. Install the `rb` command once, and you can type `rb <command>` in any folder without thinking about Docker:

| System | Run once, from the `resume-builder` folder | Installs |
| --- | --- | --- |
| macOS, Linux | `sh scripts/install-rb.sh` | `~/.local/bin/rb`, and adds that folder to your PATH in your shell profile if needed |
| Windows | Double-click `scripts\install-rb.cmd`, or run it in a terminal | `rb.cmd` in `%LOCALAPPDATA%\resume-builder\bin`, added to your user PATH; works in PowerShell and Command Prompt |

Then run `rb --help`. The rest of this manual uses `rb`; `rb <command> --help` explains each command.

Where `rb` works right after installing:

- **New terminal windows and tabs:** everywhere.
- **The terminal you installed from:** macOS and Linux, after `. ~/.bashrc` (or `~/.zshrc`; the installer names the file). Windows Command Prompt, at once.
- **A PowerShell window that was already open:** after reloading its PATH with this line: `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')`
- **VS Code's built-in terminal:** after restarting VS Code. Its terminals keep the PATH VS Code started with.

- **What it runs:** `rb` is a small script that runs `docker compose run --rm app rb <arguments>` for this folder, so your `.env` applies wherever you are. Docker must be running. The app itself doesn't have to be (`docker compose up -d`); `rb` starts a short-lived container for each command.
- **Moving the folder:** run the installer again; it updates the command. It never overwrites an `rb` command that isn't its own.
- **Removing it:** `sh scripts/install-rb.sh --uninstall` or `scripts\install-rb.cmd -Uninstall`. On Windows this also takes the folder off your user PATH, leaving the rest of your PATH exactly as it was.
- **Another folder:** set `RB_BIN_DIR` before running the installer to put the command somewhere else.
- **Without installing anything:** run `docker compose run --rm app rb <command>` from the `resume-builder` folder.
- **Windows quoting:** arguments pass through Command Prompt. For `rb call` with JSON, use the shell inside the container (below), where quoting works as usual.

Each `docker compose run` starts a new container, which takes a few seconds. For a session of many commands, open a shell inside the running app instead: `docker compose exec app bash`. There `rb` works as is and responds at once, on any operating system, with no alias needed. The app must be running (`docker compose up -d`); type `exit` to leave. The shell itself runs as root, but `rb` switches to your user (`PUID`/`PGID`) before it does anything, so the files it writes stay yours. Other commands you run in that shell don't, so open it as your own user with `docker compose exec -u rb app bash` if you plan to create or edit files there.

If you have more than one user folder, add `--user <name>`; otherwise `RB_DEFAULT_USER` is used.

### What happens at startup

Each time the container starts, it:

1. checks that the data, database, output and backup folders are mounted and writable (if not, it stops and says which one and how to fix it);
2. finds user folders (any folder in `data/` containing a `user.yaml`);
3. creates your folder from the starter files if it doesn't exist yet;
4. adds any missing files or folders, never overwriting, moving or deleting anything;
5. updates the database structure if needed (after a backup);
6. takes the daily backup if it hasn't been taken today;
7. syncs your files and writes `SYNC-STATUS.md`.

`rb doctor` runs the same checks without changing anything.

## 3. Your folder

```text
data/<you>/
  START-HERE.md           a five-step checklist
  SYNC-STATUS.md          written by the app: what's synced and what needs fixing
  .schemas/               generated; lets your editor autocomplete and check files
  user.yaml               your settings
  base.yaml               your content library
  organizations.yaml      companies and departments you've looked at
  profiles/public.yaml    what your general resume shows
  intake/                 posting text you saved from a browser, for rb new-job --file (see its README.md)
  jobs/<job-id>/
    posting.md            the job description
    application.yaml      the application record and its event history
    profile.yaml          the tailored resume for this job (ids only)
    fit-report.md         how well you match, and what's missing
    cover-letter.md       optional
    answers/why-here.md   optional "Why do you want to work here?" answer
```

Every YAML file opens with a comment explaining what it's for and what to do first, and every field has a comment with its meaning, allowed values and an example. Optional fields are present with `null` so you can see what's possible.

**Tip:** make `data/<you>/` its own private git repository. You'll get a history of every change, yours and the app's.

Outside your folder:

- **The database** lives in a Docker volume (`rb-db`). Don't look for it in your folders; use `rb backup` to get a copy.
- **`output/<you>/`** holds PDFs you've exported. It's disposable: delete it any time (`rb clean-output`), and regenerate anything from its snapshot.
- **`backups/<you>/`** holds database backups. Copy these off your computer now and then.

## 4. How your edits reach the app (sync)

### When it happens

- Within a few seconds of saving a file, while the app is running.
- At startup.
- Before any `rb` command that reads data.
- On demand: `rb sync` (or `rb sync --check` to check without applying).

Files you haven't changed are skipped, so syncing is quick.

### What's checked

Each changed file goes through these checks, in order. A file that fails any of them isn't applied, and the app keeps using its last good version.

1. **Safe reading:** UTF-8 text, under 1 MB, no invisible control characters, no duplicate keys, no YAML anchors or aliases (`&name`, `*name`), no deeper than 12 levels.
2. **Format:** required fields present, right types, allowed values, ids and dates in the right format, no unknown fields (typos are caught rather than ignored).
3. **Content rules:** unique ids, roles in order, keywords backed by tags, and so on.
4. **References:** everything a file points to exists (bullet ids, company slugs).
5. **History:** recorded events can't change, and job ids can't change.
6. **Conflicts:** your edits are compared with changes the app made since the last sync.
7. **Database check:** the change is tried inside a transaction, so the database's own rules are checked too, before it's committed.

Files are applied one at a time in this order: `user.yaml`, `organizations.yaml`, `base.yaml`, profiles, then each job. A mistake in one file doesn't hold up the others, except files that depend on it, which wait until it's fixed.

### When something's wrong

`SYNC-STATUS.md`, the website's home page and `rb sync` all show the problem:

```text
✗ base.yaml (failed)
    problem (line 23, `roles.0.bullets.1.status`): Not an allowed value. Use one of: 'draft', 'confirmed'. Example: confirmed
```

Fix the file and save. The app never stops because of a bad file.

`SYNC-STATUS.md` also lists jobs your AI agent proposed that are waiting for you (section 15), and files in `intake/` you've already used to start a job, which you can delete (section 7).

### Conflicts

Conflicts are rare, because files sync within seconds. One happens when a file has a change the app hasn't applied yet (usually because the file currently has an error) and, in the meantime, the app changes the same field through a command or the agent. Changes to different fields merge automatically, and new events from both sides are kept. For the same field, choose:

```sh
rb resolve jobs/<job-id>/application.yaml --keep file   # use what's in your file
rb resolve jobs/<job-id>/application.yaml --keep app    # rewrite the file from the database
```

### Removing things

Deleting an entry from `base.yaml` or `organizations.yaml` **archives** it: it can't be used in new documents, but documents that used it are unaffected. Deleting a job folder deletes nothing; `rb export-files` recreates it, and `rb archive-job <job-id>` archives the application.

If you delete `base.yaml` (or another main file) by accident, the next start rewrites it from the database.

### Backups before changes

Before any sync that changes data, the database is backed up automatically (section 14).

## 5. Your content library: base.yaml

`base.yaml` holds every claim that can ever appear on a resume, cover letter or answer. A profile picks what goes on each document, so put everything here, even things you wouldn't use every time.

The file has six sections, in this order. Every section is a list; leave it as `[]` if you have nothing for it.

| Section | What goes in it |
| --- | --- |
| `summary_phrases` | Building blocks for the summary at the top of a resume. |
| `roles` | Jobs, newest first, each with its bullets. |
| `projects` | Personal, lab or volunteer work, each with its bullets. |
| `education` | Degrees and schools. |
| `certifications` | Certifications and licenses. |
| `skills` | Named groups of skills. |

The file also starts with `schema_version: 1`. Leave it as it is.

**Rules for every section:**

- **ids** are lowercase letters, digits and hyphens (`tcdi-ansible-patching`), unique across the whole file. Never reuse one, even after deleting the item: deleted items are archived, not erased.
- **Dates** are `YYYY-MM` (`2022-07`) or `YYYY` (`2022`). An end date can also be `present`. An end date before its start date is an error.
- **Unknown keys are errors**, so a typo like `titel:` is caught instead of silently ignored.
- **Text length** is limited to 300 characters for short fields (titles, names, locations) and 5,000 for long ones (bullet text, descriptions).
- **Comments** (`# ...`) are kept when the app writes the file back. Use them for notes that have no field, such as a short description of a company. Comments never appear on documents.

Not every item has a `status`. Bullets, summary phrases, education and certifications do, and only confirmed ones reach real documents. Roles, projects and skill groups don't: a role or project appears when a profile picks one of its confirmed bullets, and a skill group appears when a profile lists it.

### Summary phrases

Short sentences a profile combines into the summary paragraph. The phrases a profile picks are joined into one paragraph in the order it lists them, so write each as a complete sentence that reads well next to the others.

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique id. |
| `text` | yes | One sentence. |
| `tags` | no | Keywords this phrase supports, lowercase-with-hyphens. |
| `status` | no | `draft` (default) or `confirmed`. |

```yaml
summary_phrases:
  - id: summary-platforms
    text: Infrastructure automation engineer who builds and runs shared CI/CD platforms.
    tags: [infrastructure-automation, ci-cd]
    status: draft
```

### Roles

One entry per job, newest first. A resume shows the title, then the official title on its own line if there is one, then company, location and dates ("Jul 2022 – Aug 2024"), then the company description.

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique id. |
| `title` | yes | The title that describes the work you did. |
| `official_title` | no | Your official title, if different. Shown under the title as "Official title: …". `null` to leave it off. |
| `company` | yes | The employer's name as you want it shown. |
| `location` | no | City and state, or `Remote`. `null` to leave it off. |
| `start` | yes | `YYYY-MM` or `YYYY`. |
| `end` | no | `YYYY-MM`, `YYYY` or `present` (the default). |
| `description` | no | One line about the employer, shown in italics under the company line: "TCDI is a legal services and cybersecurity solutions company." `null` to leave it off. |
| `bullets` | no | The claims for this job (see [Bullets](#bullets)). |

```yaml
roles:
  - id: example-co-platform
    title: Platform Engineer
    official_title: Systems Engineer II
    company: Example Co
    location: Austin, TX
    start: 2021-03
    end: present
    description: Example Co makes billing software for clinics.
    bullets:
      - id: example-co-ci
        text: Cut release time from 3 hours to 25 minutes by moving builds to GitHub Actions.
        tags: [github-actions, ci-cd]
        metric: 3 hours to 25 minutes
        evidence: Release logs before and after the move.
        limits: Two of the five product teams.
        status: draft
```

### Projects

Personal, lab, open-source or volunteer work. A resume shows the name, the dates (only if `start` is set), the description and the bullets.

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique id. |
| `name` | yes | The project's name. |
| `start` | no | `YYYY-MM` or `YYYY`. `null` to show no dates. |
| `end` | no | `YYYY-MM`, `YYYY` or `present`. Shown as `present` when `start` is set and this is `null`. |
| `description` | no | One line of context under the name, such as a link to documentation. |
| `bullets` | no | The claims for this project (see [Bullets](#bullets)). |

```yaml
projects:
  - id: homelab
    name: Home lab
    start: 2023-01
    end: present
    description: "Documentation: https://homelab.example.com/"
    bullets:
      - id: homelab-proxmox
        text: Run a three-node Proxmox cluster with automated backups and alerting.
        tags: [proxmox, backups]
        metric: null
        evidence: null
        limits: Personal environment, not production.
        status: draft
```

Put a value in quotes when it contains `: ` (a colon followed by a space), as in the `description` above. Otherwise YAML reads it as a new key.

### Bullets

Bullets sit in the `bullets:` list of a role or project. They're the main content of a resume, and the only thing letters and answers may cite.

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique id. Letters, answers and fit reports cite bullets by this id. |
| `text` | yes | The claim, outcome first: "Cut release time from 3 hours to 25 minutes by…" Wrap words in `**double asterisks**` to show them in bold: `Moved builds to **GitHub Actions**`. The ATS version and the AI agent brief use the plain text. |
| `tags` | no | Keywords this bullet supports (`python`, `github-actions`). Profiles may only claim keywords that are tags. |
| `metric` | no | A number you can back up. Leave `null` rather than guess. |
| `evidence` | no | Where the claim comes from. Never shown on documents. |
| `limits` | no | How far the claim goes ("Linux only"). Never shown; AI agents must respect it. |
| `status` | no | `draft` (default) until you've checked it, then `confirmed`. |

### Education

A resume shows "degree, field — institution (end year)", leaving out whatever is empty. The start date is kept for your records but not shown.

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique id. |
| `institution` | yes | The school's name. |
| `degree` | no | e.g. `B.S.` or `Bachelor of Science`. `null` if not applicable. |
| `field` | no | e.g. `Computer Engineering`. |
| `start` | no | `YYYY-MM` or `YYYY`. Not shown. |
| `end` | no | Graduation, `YYYY-MM` or `YYYY`. `null` to leave the date off. |
| `status` | no | `draft` (default) or `confirmed`. |

```yaml
education:
  - id: state-university-bs
    institution: State University
    degree: B.S.
    field: Computer Science
    start: null
    end: 2012
    status: draft
```

### Certifications

A resume shows "name, issuer (date)", leaving out whatever is empty.

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique id. |
| `name` | yes | The certification's name. |
| `issuer` | no | Who issued it. |
| `date` | no | When you earned it, `YYYY-MM` or `YYYY`. |
| `status` | no | `draft` (default) or `confirmed`. |

```yaml
certifications:
  - {id: rhcsa, name: RHCSA, issuer: Red Hat, date: 2023-05, status: draft}
```

### Skills

Named groups of skills, shown as the group name followed by its items. A profile lists the groups to show, in order. Skill groups have no status, so check them before a profile uses them. Prefer skills a bullet backs up: a fit report can't cite a skill that no bullet supports.

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique id. Profiles refer to the group by this. |
| `name` | yes | The heading, e.g. `CI/CD & Developer Platforms`. |
| `items` | no | The skills, in the order to show them. |

```yaml
skills:
  - id: skills-ci-cd
    name: CI/CD
    items: [Jenkins, TeamCity, GitHub Actions]
```

### Editor help

The first line of `base.yaml` links a schema generated from the app's own models: `# yaml-language-server: $schema=.schemas/base.schema.json`. In VS Code with the **YAML** extension (by Red Hat), and in other editors that use the YAML language server, it gives autocomplete, a description when you hover over a field, and a red underline on unknown keys and invalid values as you type. Keep that line at the top of the file.

### Writing and confirming

**Writing tips:** lead with the outcome, then the evidence. Use real tool names. Avoid filler like "proven track record" (the banned phrases list in `user.yaml` catches these in letters).

You can confirm an item from the command line too: `rb confirm <id>`. Only bullets, summary phrases, education and certifications have a status. Roles, projects and skill groups don't: a role or project shows on a resume once a profile picks one of its confirmed bullets, so confirm the bullets (giving a role's id to `rb confirm` lists its draft bullets). Example entries (`example-`) can't be confirmed; replace them.

To confirm everything at once, run `rb confirm --all`. It lists every draft bullet, summary phrase, education and certification, then asks you to type `yes` before changing anything; any other answer leaves everything as it was. Only items on that list are confirmed, so nothing added in the meantime slips through. Use it after you've reviewed the whole library, for example right after importing your resume, not as a shortcut past checking. Add `--yes` to skip the question in a script.

## 6. Companies and departments

`organizations.yaml` records each company once, with its departments, so job ids stay consistent and you're offered existing names instead of retyping them.

- `rb new-job` suggests companies that match what you type, including aliases, and ignoring case, punctuation and endings like Inc, LLC or Corp. Add a new one only when nothing matches.
- In VS Code, the `company` and `department` fields in `application.yaml` autocomplete from your list.
- `rb orgs` lists them. `rb add-company "Name"`, `rb add-department <company> "Name"`.
- `rb rename-org <company> "New name"` renames and keeps the old name as an alias. Job ids keep the original slug.
- `rb merge-orgs <keep> <duplicate>` merges a duplicate and moves its applications.

## 7. Tracking a job

### Starting

One command starts every job: `rb new-job`. Give it the posting's link and it reads the posting for you:

```sh
rb new-job https://careers.example.com/jobs/1234
```

It fetches the page once, keeps a copy of the posting exactly as published, and fills in what the posting states: title, link, source, posting id, location, remote/hybrid/onsite, salary range as posted, date posted and closing date. It shows you a preview first:

- each value, and where it came from (the page's own job data, the posting text, or you);
- the company and department, matched against your registry (section 6);
- what the posting doesn't state (left empty, never guessed);
- anything to check, for example a date that the page's data and its text give differently;
- any job you're already tracking with the same link or posting id.

Then it asks once: press Enter to create the job, or type a field name to change that value first. If the company isn't in your registry, it offers the closest matches or to add it. Nothing is created until you confirm.

The job gets `application.yaml` with those fields, and `posting.md` with the posting, a **Source** line (link and date captured) and a **My notes** section for you.

**What's never guessed.** A value is filled in only when the posting states it, and every value is checked against the words of the posting before it's used:

- A field the posting doesn't mention stays empty. The preview lists them under "Not stated in the posting".
- Remote, hybrid or onsite is set only when the posting says exactly one of them. If it mentions more than one, it's left for you to set, and the preview says why.
- The salary is the range as posted, not an estimate. The company is filled in only when the page names it; otherwise you pick it.
- The posting's wording is never changed in `posting.md`: only its layout is turned into plain text.
- `interest_notes` (why you want the job) is never filled in. That's yours to write, and letters use only that for motivation.

Values you type yourself (`--set`, `--title`, or a field you change in the review) are yours and aren't checked against the posting.

Options (`rb new-job --help`):

```sh
rb new-job <url> --company northwind-labs --department platform-engineering   # choose them up front
rb new-job <url> --company "Fabrikam" --create                                  # add a new company
rb new-job <url> --title "Platform Engineer II"                                 # your own title instead of the posting's
rb new-job <url> --set work_arrangement=hybrid --set salary_range=              # your own value; empty clears one
rb new-job <url> --yes                                                          # accept the preview without asking
```

`--url <url>` works the same as giving the link on its own.

**When the link can't be read.** Some sites need you to sign in (LinkedIn, for example) or show their postings only with JavaScript. `rb new-job` then stops, says why, and creates nothing. Copy the posting text from your browser into a text file in your **intake folder** (`data/<you>/intake/`), then point `rb new-job` at it, with the link if you have it:

```sh
rb new-job --file intake/acme-sre.txt https://www.linkedin.com/jobs/view/123
```

The review works the same as with a link. The app reads a file in `intake/` only when you run this; it never changes, moves or deletes it. Once the job is created, the posting is kept with the job, so you can delete the file; `SYNC-STATUS.md` lists the files you've already used. The file must be UTF-8 text and smaller than 1 MB.

You can also pipe the text in: `rb new-job --stdin <link> < posting.txt`. Piped input can't also answer questions, so this shows the preview; run the same command with `--yes` (and `--company` if needed) to create the job.

**Already tracking it?** If a job has the same link (tracking parameters ignored) or the same company and posting id, `rb new-job` says so. In a terminal it offers to save the new copy of the posting for that job instead (`--recapture <job-id>`); `--allow-duplicate` starts a separate job anyway. Re-capturing replaces the posting part of `posting.md` and keeps your notes. If you pasted or edited the posting text yourself, it's left alone unless you add `--replace`.

**Let your AI agent propose it.** An MCP or API agent given the link can propose the job instead; you review it with `rb confirm-job <n>`, which works like the review above (section 15).

**No link and no text?** Without a link, `--file` or `--stdin`, `rb new-job` just creates the job: it asks for the company and department and seeds the folder. Paste the posting into `posting.md` and fill in `application.yaml` yourself.

```sh
rb new-job --title "Platform Engineer"            # asks for company and department
rb new-job --company "Acme Corp" --department "Platform" --title "SRE" --create
```

Settings in `user.yaml` under `intake`: `fetch: false` turns fetching off (then use `--file` or `--stdin`), and `timeout_seconds` (default 20) and `max_bytes` (default 2000000, about 2 MB) limit how long it waits for a site and how much it reads.

The job id is built for you: **date entered + company + department**, for example `2026-11-13-acme-corp-platform`. It never changes.

### application.yaml

Fill in what you know. The useful ones:

- `interest_level` (1–5) and **`interest_notes`**: why you want the job, in your words. Letters and answers use *only* this for motivation.
- `next_action` and `next_action_due`: shown on the website's home page, sorted by date.
- `contacts`: recruiters, hiring managers, interviewers, referrers.
- `source`, `salary_range`, `work_arrangement`: used by the dashboard.

### Events

`events` is the history. Add new events **at the end** (or use `rb event`). Recorded events can't be edited or removed; to fix a mistake, add a `correction` event.

```yaml
  - date: 2026-11-20
    type: stage_change        # stage_change | interview | follow_up_sent | message_received | note | document_sent | correction
    stage: screening
    notes: Phone screen booked with Sam.
```

```sh
rb event <job-id> --stage applied --documents <snapshot-ids>
rb event <job-id> --type interview --notes "Panel with platform team"
rb event <job-id> --type correction --corrects 3 --stage screening --notes "Logged the wrong stage"
```

### Stages

```text
interested → tailoring → applied → acknowledged → screening → interviewing → offer → accepted / declined
withdrawn: from any open stage
rejected, ghosted: from applied onwards
```

You can skip ahead (for example `applied` straight to `interviewing`) but not go back. `accepted`, `declined`, `rejected`, `withdrawn` and `ghosted` are final; only a `correction` event changes them.

An application with no event for 14 days (`tracker.stale_days`) is flagged **stale**. The app never changes a stage by itself; you decide whether it's `ghosted`.

## 8. Tailoring a resume

A tailored resume is `jobs/<job-id>/profile.yaml`: ids from `base.yaml`, in order.

```yaml
summary: [sum-platform]
roles:
  role-fabrikam: [fab-ci-pipeline, fab-monitoring]
  role-tailspin: [tail-docker]
projects: {}
skills: [sk-automation, sk-observability]
keywords: [github-actions, prometheus]      # each must be a tag in base.yaml
earlier_roles:                              # older roles, condensed into one entry
  role-old-job: []
omitted_roles: []                           # roles you've chosen to leave out, each with a reason
gaps:
  - skill: Kubernetes
    note: Not in base.yaml. Real gap, or missing information?
```

Rules the app enforces:

- Every id must exist. Drafts are allowed in a preview only.
- No text of its own except under `gaps`.
- Roles newest first. Every role is either under `roles`, condensed under `earlier_roles`, or in `omitted_roles` with a reason, so there's no unexplained gap in your history.
- `keywords` must be tags on your bullets.

**Earlier roles.** Roles under `earlier_roles` appear after the others as one compact "Earlier Experience" entry: the companies and the overall date span, then each role's title, company and dates on one line, with any bullets you list for it underneath. It uses only those roles' own titles, companies, dates and confirmed bullets, so it adds no new text. They must all be older than the roles under `roles`. Use it to keep a long history visible without giving old jobs full space; AI agents condense older roles this way rather than dropping them.

The **fit report** (`fit-report.md`) lists strong matches, weak spots, gaps, questions for you, and notes on the role. For each gap, decide: is it a real gap, or missing information? If it's missing information, add it to `base.yaml` (as a draft first), and every future application benefits.

You can write profiles yourself or let an AI agent do it (section 15). When you save a new profile, any approved letter or answer for that job goes back to draft, because it may no longer match.

## 9. Cover letters and application answers

Letters and "Why do you want to work here?" answers are built only from **that job's tailored resume**, the posting, and your `interest_notes`.

Every sentence that makes a claim about you ends with a citation:

```text
I cut release time from 3 hours to 25 minutes by moving 14 services to GitHub Actions [cite: fab-ci-pipeline].
```

Citations are removed from the finished document. The checks:

- every cited id is on this job's tailored resume and confirmed;
- every number and tool in a sentence appears in a bullet it cites;
- a sentence that mentions a number or one of your tags/skills has a citation (words you used in your `interest_notes` are fine without one, since that's where motivation comes from);
- word limits: answers up to 100 words; letters 250–350 (set in `user.yaml` → `writing`);
- no banned phrases;
- answers are plain prose (no headings or bullet points);
- `[MOTIVATION NEEDED]` must be replaced before approval.

Drafts from an AI agent must pass every check. When **you** edit the text (just edit the `.md` file), problems become warnings, recorded with the version, because you're the author.

```sh
rb approve <job-id> cover_letter
rb approve <job-id> answer                 # answers/why-here.md
rb approve <job-id> answer --slug other    # answers/other.md
```

Approving saves a snapshot and gives you its id. Editing an approved document makes it a draft again.

## 10. Rendering, snapshots and exact copies

```sh
rb render <job-id>                         # tailored resume, PDF
rb render <job-id> --ats                   # plain single-column version for applicant-tracking systems
rb render <job-id> --type cover_letter
rb render <job-id> --type answer --format txt
rb render public --preview                 # includes drafts, stamped "PREVIEW"
rb render <job-id> --format html           # or none (snapshot only)
```

Each render saves a **snapshot**: the exact text of every section, which bullets were used, the design (template) version, the app version and when it was made. Your email and phone are *not* in it; they're added when the PDF is built, and you're told if they've changed since.

Identical content gives the identical snapshot id, so nothing is stored twice.

```sh
rb snapshots <job-id>                      # list them; "protected" ones are never auto-deleted
rb regenerate <snapshot-id>                # rebuild exactly
rb regenerate --job <job-id> --type resume --as-of 2026-11-20   # what it looked like on that date
rb diff <snapshot-a> <snapshot-b>          # what changed between two versions
rb pin <snapshot-id> / rb unpin <snapshot-id>
rb delete-snapshot <snapshot-id> [--confirm]
```

**Exactly the same:** with the same app version, the regenerated PDF is byte-for-byte identical. After an update, the text and design are still identical, but the app warns that small layout differences are possible (for example from a font update).

**Limits:** at most 10 snapshots per document type per job, and 1,000 in total (`user.yaml` → `snapshots`). When a limit is reached, the oldest unprotected one is removed. **Protected:** sent with an application, approved, pinned, or your current public site. If everything is protected and the total limit is reached, the app refuses to create a new document and tells you how to make room.

PDFs are exported to `output/<you>/<job-id>/`. Nothing there is kept by the app.

**Design.** The `classic` template's version 2 (the default) has two looks. The normal render is a styled print layout: name and company names in blue, positions in capitals, official titles and company descriptions in italics, bold where your bullets use `**`. The `--ats` render is plain black, single column, with underlined section headings and bullet characters kept in the text, so applicant-tracking systems read it in order. Both print your links as visible addresses (`github.com/you` in the styled version, the full URL in the ATS version), so they survive printing. Sections come in this order: Professional Summary, Work Experience, Core Skills, projects (a single project's name becomes the section heading), Education, Certifications. To keep the older layout, set `template_version: 1` in `user.yaml`; documents keep the version they were made with either way.

## 11. Recording what you sent

When you apply, record the snapshots you sent:

```sh
rb event <job-id> --stage applied --documents <resume-id> <letter-id> <answer-id>
```

Or add the `applied` event in `application.yaml` with a `documents:` list. The app fills in `submission` and protects those snapshots. Preview snapshots can't be sent.

## 12. The tracker website

Open [http://localhost:8000](http://localhost:8000) (or your `RB_HOST_PORT`). It's reachable only from your computer, and read-only until you sign in.

**Signing in** lets you confirm or discard what your AI agent proposed right on the site, instead of running `rb confirm-job` and friends. Run `rb web-login` and open the link it prints (it works once, for 10 minutes), or use **Sign in** at the top of any page with an owner token (`rb token --actor owner`); agent tokens can't sign in. You stay signed in on that browser for up to 7 days, or until 12 hours without using the site; **Sign out** ends it at once. Nothing else on the website changes your data.

- **Home:** sync status and anything needing attention, whether your AI agent is set up and when it was last active, upcoming actions (overdue in red), stale applications, proposed events and jobs waiting for you.
- **Applications:** filter by stage, company, department; sort by any column.
- **Application:** posting, details, contacts, the full timeline (who recorded each event and when), documents and their status, every snapshot (open as PDF or HTML, compare any two), how this resume differs from your public one, and every saved copy of the posting with the date it was captured (shown as text, never as the original web page).
- **Company:** every application to that company over time.
- **Proposed jobs:** jobs your AI agent proposed, each with every value it found, where the posting says it, the company match and any job you already track. Signed in, each has a form to check the company, department and any value, handle a duplicate, and **Confirm** (or **Discard**); it does exactly what `rb confirm-job` does. Not signed in, each shows the `rb confirm-job <n>` and `rb discard-job <n>` commands to run. Proposed events on a job's page get **Confirm** and **Discard** buttons the same way.
- **Dashboard:** applications per week, response rate, median days to first response, how far applications got, outcomes by source, response rate with and without a cover letter, and the bullets that appear most in resumes that reached interviews.

## 13. Your public resume

`profiles/public.yaml` selects what goes on your general resume.

```sh
rb render public                           # PDF
rb publish                                 # static web page: output/<you>/public/index.html
```

The web page never includes your email or phone. Copy it to your website host. Set `publish.enabled` and `publish.target` in `user.yaml` to record where it goes. Pushing it there automatically isn't built yet.

## 14. Backups and restoring

The database is the official record, so it's backed up:

- automatically before every sync or update that changes data;
- once a day;
- whenever you run `rb backup`.

The last 30 of each kind are kept (`backups.keep`). They're in `backups/<you>/`. **Copy them somewhere else regularly.**

```sh
rb backups                                 # list
rb restore app-daily-20261120T080000Z.db   # restore (the current database is backed up first)
rb sync                                    # re-apply any newer edits from your files
```

### Backing up everything at once

The database lives in a Docker volume, not in a folder you can copy, and your own files are in `data/`. `scripts/backup.py` collects both into one zip file. It needs only Python 3.8 or newer and works the same on Windows, macOS and Linux. Run it from the resume-builder folder:

```sh
python scripts/backup.py                                   # zip goes to ~/resume-builder-backups
python scripts/backup.py --dest D:/Backups/resume-builder  # somewhere that's itself backed up or synced
```

It asks the app for a fresh database backup (`rb backup`) for every user, then zips your `data/` folder and the newest database backup per user, with a `RESTORE.txt` explaining how to put them back. It keeps the newest 10 zips (`--keep`). `output/` is left out, because every export can be rebuilt from its snapshot.

| Option | What it does |
| --- | --- |
| `--dest <folder>` | Where the zip goes. Default `~/resume-builder-backups`. |
| `--keep <n>` | How many zips to keep. Default 10. |
| `--include-env` | Also include `.env`. It holds your contact details, so only use a destination you trust. |
| `--all-backups` | Include every database backup, not just the newest. |
| `--skip-db-backup` | Don't ask the app for a fresh database backup first (for example when Docker isn't running). |

Schedule it to run daily with Task Scheduler (Windows), launchd (macOS) or cron (Linux), and point `--dest` at an external drive or a cloud-synced folder.

### Restoring a backup zip

`scripts/restore.py` puts a zip made by `backup.py` back, on this computer or a new one (clone resume-builder and create `.env` first):

```sh
python scripts/restore.py                                  # the newest zip in ~/resume-builder-backups
python scripts/restore.py D:/Backups/resume-builder/resume-builder-backup-20261120-080000.zip
```

It shows what it will replace and asks you to type `yes`; any other answer changes nothing. Then it:

1. stops the app if it's running, so files and database are never synced half-restored;
2. saves your current files as `backups/<you>/pre-restore-files-<date-time>.zip`;
3. replaces `data/<you>/` with the copy from the zip;
4. restores the database with `rb restore`, which first saves the current one as `app-pre-restore-….db`;
5. starts the app again, and its startup sync checks everything.

Nothing is lost: if you restored the wrong zip, the two pre-restore copies hold what you had.

| Option | What it does |
| --- | --- |
| `--dest <folder>` | Where to look for the newest zip when you don't name one. |
| `--user <name>` | Restore only this user (repeat for several). Default: every user in the zip. |
| `--files-only` | Restore `data/<you>/` only. |
| `--db-only` | Restore the database only. |
| `--include-env` | Also restore `.env`; the current one is kept as `.env.pre-restore-<date-time>`. |
| `--yes` | Don't ask (for scripts). Without it, the script refuses to run when it can't ask. |

## 15. Working with an AI agent

The app has no built-in AI and needs no paid AI plan: copy and paste works with any free chat assistant, and some assistants that connect directly have free plans too (see [AI-AGENTS.md](AI-AGENTS.md#do-i-need-a-paid-ai-plan)). You use your own assistant, and it works as the **agent**: it can read your confirmed content and propose drafts, but it can't confirm content, approve letters, record events or create jobs (only propose them), publish, or change companies. Every draft passes the honesty checks.

There are three ways to connect it:

- **MCP tools** (Claude Code, Claude Desktop, Gemini CLI, Codex CLI, GitHub Copilot in VS Code, Copilot CLI and others): the assistant calls resume-builder directly through `rb mcp`.
- **Copy and paste** (claude.ai, Gemini, ChatGPT, Microsoft Copilot): paste `rb brief <job-id>` in, and pipe each block it returns into `rb propose`.
- **HTTP API** for your own scripts, with a token from `rb token --actor agent`.

**Step-by-step instructions for each assistant are in [AI-AGENTS.md](AI-AGENTS.md).**

**Is it connected?** The website's home page has an **AI agent** card, and `rb status` has a line, that say whether an agent is connected and through which channel, when it was last active, and what it last did. "Used through copy and paste" means you've pasted drafts with `rb propose`, but no assistant has connected directly (that's fine if copy and paste is how you work). "No AI agent has connected yet" means nothing has reached the app: check your assistant's setup in [AI-AGENTS.md](AI-AGENTS.md), then ask it to call `agent_brief`. "Token issued, not used yet" means the HTTP API token hasn't been used. If your assistant keeps using an API token you've since replaced with `rb token`, the card says so.

Review proposed events with `rb event-drafts`, then `rb confirm-event <n>` or `rb discard-event <n>`.

An MCP or API agent can also **propose a new job**: give it the posting's link and it calls `job_intake_propose`. The app fetches the posting itself and rejects any value the posting doesn't state. Nothing is created until you review it: `rb job-drafts` lists what's waiting (so do `rb status` and `SYNC-STATUS.md`), and `rb confirm-job <n>` shows the same preview and questions as `rb new-job <url>` (it takes the same options, such as `--company` and `--yes`). `rb discard-job <n>` drops one. Everything the agent does is in the audit log (`rb audit`), marked as the agent.

### Answering the agent's questions

The agent asks when your library doesn't say something it needs: a missing metric, whether a requirement is a real gap, why you want the job. It never guesses, so its questions are the way to fill those holes. They also stay in the job's `fit-report.md` under "Questions for the user", so you can answer later.

**Answer in the chat, in one message, numbered like its questions.** Answer only what you know; "skip" or "not sure" is fine.

```text
1. Bachelor of Science in Computer Engineering.
2. Real gap: I haven't used Kubernetes in production.
3. Missing information: the pipeline work cut build time from 40 to 12 minutes.
4. Skip for now.
```

- For a gap, say which it is: a **real gap** (you don't have it; it stays a gap) or **missing information** (you do; give the facts).
- Give exact numbers, or say there isn't one. The agent never estimates a metric.

**Where each answer ends up:**

| Your answer | What happens |
| --- | --- |
| New experience or a fact missing from your library (a tool, a project, an outcome) | The agent saves it as a **draft** bullet in `base.yaml`. It's used only after you confirm it: `rb confirm <id>`, or `status: confirmed` in `base.yaml`. |
| A metric or detail for a bullet that's already confirmed | Add it to that bullet in `base.yaml` yourself; the agent can't change confirmed content. |
| Why you want the job | Write it in `interest_notes` in the job's `application.yaml`, in your own words; rough notes are fine. The agent turns them into the "Why do you want to work here?" answer and the cover letter, but never writes the reasons for you (without notes it writes `[MOTIVATION NEEDED]`). |

Then ask it to redo the work, for example: "Update the profile and fit report for `<job-id>` with my answers." It reads the brief again, picks up what you confirmed, and rewrites the drafts.

## 16. Command reference

| Command                                                                              | What it does                                        |
| ------------------------------------------------------------------------------------ | --------------------------------------------------- |
| `rb status`                                                                        | Sync status, problems, example entries left, limits |
| `rb sync [--check]`                                                                | Apply (or just check) changed files                 |
| `rb resolve <file> --keep file\|app`                                                | Settle a sync conflict                              |
| `rb doctor`                                                                        | Check everything; change nothing                    |
| `rb init-user <name>`                                                              | Create another user folder                          |
| `rb export-files`                                                                  | Recreate missing files from the database            |
| `rb new-job <url> [--company --department --title --create --set F=V --yes]`       | Start a job from its posting link, reviewed once    |
| `rb new-job --file intake/<name>.txt [<url>]`                                      | The same, from posting text saved in `intake/`      |
| `rb new-job --stdin [<url>] < posting.txt`                                         | The same, from piped posting text                   |
| `rb new-job <url> --recapture <job-id> [--replace]`                                | Save a new copy of a posting for an existing job    |
| `rb new-job [--company --department --title --source --create]`                    | Start a job without a link (paste the posting later) |
| `rb list [--stage --company --all]`                                                | List applications                                   |
| `rb show <job-id>`                                                                 | One application in full                             |
| `rb event <job-id> [--type --stage --date --notes --documents --corrects]`         | Add an event                                        |
| `rb job-drafts` / `confirm-job <n>` / `discard-job <n>`                        | Jobs proposed by the agent                          |
| `rb event-drafts` / `confirm-event <n>` / `discard-event <n>`                  | Events proposed by the agent                        |
| `rb archive-job <job-id> [--restore]`                                              | Archive an application                              |
| `rb orgs` / `add-company` / `add-department` / `rename-org` / `merge-orgs` | Companies and departments                           |
| `rb confirm <id>`                                                                  | Confirm a draft library item                        |
| `rb confirm --all [--yes]`                                                         | Confirm every draft, after listing them and asking  |
| `rb brief <job-id>`                                                                | The agent brief                                     |
| `rb render <job-id\|public> [--type --preview --ats --format]`                      | Snapshot and export a document                      |
| `rb approve <job-id> cover_letter\|answer [--slug]`                                 | Approve a letter or answer                          |
| `rb snapshots [<job-id>] [--type]`                                                 | List snapshots                                      |
| `rb regenerate <id>` or `--job --type --as-of`                                   | Rebuild a document exactly                          |
| `rb diff <a> <b>`                                                                  | Compare two snapshots                               |
| `rb pin` / `unpin` / `delete-snapshot`                                         | Manage snapshots                                    |
| `rb publish`                                                                       | Export the public site                              |
| `rb clean-output`                                                                  | Empty the export folder                             |
| `rb dashboard` / `rb actions`                                                    | Statistics; upcoming and stale                      |
| `rb token --actor agent\|owner`                                                     | Issue an API token                                  |
| `rb web-login`                                                                     | Print a one-time link that signs you in to the website |
| `rb backup` / `backups` / `restore <file>`                                     | Backups                                             |
| `rb audit`                                                                         | Recent audit log                                    |
| `rb ops` / `rb call <operation> '<json>'`                                        | List or call any API operation directly             |

## 17. Privacy and security

- Your data stays on your computer: your folder, the database volume and backups. Nothing is sent anywhere by the app.
- The one thing the app fetches from the internet is a job posting, when you run `rb new-job <url>`: a single request to that link, with no cookies and nothing about you. Links that point to your own network or computer are refused. Turn it off with `intake.fetch: false` in `user.yaml`.
- The website and API listen only on `127.0.0.1`, so other computers can't reach them.
- Your email and phone live only in `.env` (or Docker secrets). They're not in your files, the database, snapshots, logs or the public site.
- The Docker image contains no user data; it's excluded at build time.
- YAML is read with strict safety limits, so a malformed or malicious file can't run code or exhaust memory.
- Every change is in the audit log with who made it (you, the agent, or the system) and how (file sync, command, API).
- Tokens protect against mistakes and keep the agent within its role. They aren't protection against someone who already controls your computer or Docker.
- The website and API answer only when you open them as `localhost` or `127.0.0.1` (or a name in `RB_WEB_HOSTS`). That stops another website you visit from reading your tracker through your browser by pretending to be on your computer.
- Signing in to the website gives your browser a session cookie that only works on your computer, can't be read by page scripts, and ends after 12 hours idle or 7 days. Its buttons only work from the tracker's own pages: a form on another website can't use them.
- Job ids contain employer names. If you keep your folder in git, keep that repository private.

## 18. Troubleshooting

| Symptom                                           | What to do                                                                                                              |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| The container stops at start with "not writable"  | Check `PUID`/`PGID` in `.env` match `id -u`/`id -g`, and that the folders in `RB_HOST_*` exist.             |
| My edit didn't take effect                        | Look at `SYNC-STATUS.md` or `rb status`; the file probably has a problem and the last good version is still in use. |
| "Changed both in this file and by the app"        | A conflict: `rb resolve <file> --keep file` or `--keep app`.                                                        |
| "still a draft, so only a preview can include it" | Set `status: confirmed` in `base.yaml` once it's accurate, or use `--preview`.                                    |
| "Claim without a citation"                        | End the sentence with `[cite: bullet-id]`, or remove the number/tool from it.                                         |
| "can't move from X to Y"                          | Stages only move forward; see section 7. Use a `correction` event for mistakes.                                       |
| Contact details missing from PDFs                 | Set `RB_CONTACT_EMAIL`/`RB_CONTACT_PHONE` in `.env`, then `docker compose up -d`.                               |
| "That token isn't valid"                          | It was replaced. Issue a new one: `rb token --actor agent`.                                                           |
| "This address isn't allowed" in the browser       | Open the tracker as `http://localhost:8000` (or your `RB_HOST_PORT`). To use another name for this computer, add it to `RB_WEB_HOSTS` in `.env`, then `docker compose up -d`. |
| A job folder disappeared                          | Nothing was deleted: `rb export-files`.                                                                               |
| `rb new-job <url>` says the page needs a sign-in or JavaScript, or has too little text | Copy the posting from your browser into `intake/<name>.txt`, then `rb new-job --file intake/<name>.txt <link>`. |
| `rb new-job <url>` says "already tracking this posting" | Use `--recapture <job-id>` to update that job's posting, or `--allow-duplicate` for a separate job.                    |
| Something else                                    | `docker compose logs app`, then `rb doctor`.                                                                        |

## 19. What isn't built yet

- Pushing the public site to its target automatically (`rb publish` exports it; you copy it).
- More than one design template (`classic` only).
- Importing an existing resume into `base.yaml`.
- File changes are detected by checking every few seconds, not by instant notification. It works the same across every operating system's Docker mounts.
