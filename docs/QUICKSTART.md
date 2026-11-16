# Quick start

Get resume-builder running and produce your first tailored resume in about 15 minutes. For everything else, see the [user manual](USER-MANUAL.md).

## You need

- Docker Desktop (macOS or Windows) or Docker Engine with the Compose plugin (Linux)
- A text editor. VS Code with the YAML extension is best: it autocompletes and checks your files as you type.

## 1. Configure

```sh
git clone <this repository> resume-builder
cd resume-builder
```

Run the setup script once. The first time, it creates your settings file `.env` and stops:

- **macOS and Linux:** `sh setup.sh` (it also fills in `PUID` and `PGID` for you)
- **Windows:** double-click `setup.bat`, or run `.\setup.bat` in PowerShell (`setup.bat` in Command Prompt). In PowerShell the `.\` is needed: without it, `setup` runs a different command.

Open `.env` and set:

| Setting | What to put |
| --- | --- |
| `RB_DEFAULT_USER` | Your folder name: lowercase letters, digits and hyphens, e.g. `jamie-doe` |
| `RB_CONTACT_EMAIL`, `RB_CONTACT_PHONE` | Shown on your documents. Kept only here, never in your data. |
| `PUID`, `PGID` | Output of `id -u` and `id -g` (macOS is usually `501` and `20`). On Windows, keep the defaults `1000` and `1000`. |
| `TZ` | Your time zone, e.g. `America/New_York` |

## 2. Start

Run the setup script again: `sh setup.sh`, or `setup.bat` on Windows. It builds and starts the app (`docker compose up -d --build`) and installs the `rb` command, so you can type `rb` from any folder instead of a Docker command.

Open <http://localhost:8000>. The app has created your folder at `data/<your-name>/`. This guide, the user manual and the AI agent guide are also under **Help** at the top of every page.

Check that `rb` works: `rb --help`. If `rb` isn't found yet, open a new terminal; the setup script also prints how to make it work in a window that was already open.

Running many commands in a row? Open a shell inside the running app instead, where `rb` works directly and each command starts instantly: `docker compose exec app bash` (type `exit` to leave).

## 3. Fill in your details

In `data/<your-name>/`:

1. **`user.yaml`**: set `display_name`, `headline` and `location`.
2. **`base.yaml`**: replace the `example-` entries with your own roles, bullets, projects, education and skills. Each field has a comment explaining it. **Have a resume already?** Save it as a Word document (.docx), PDF, HTML, Markdown or plain text in `data/<your-name>/intake/` and run `rb import-resume intake/resume.docx`, or give its link: `rb import-resume https://example.com/resume` (both the same on Windows): it shows what it found, asks once, and adds everything as drafts, in your words, without changing anything you already have (user manual, section 5). Either way, set `status: confirmed` on everything you've checked is accurate: bullets, summary phrases, education, certifications and skill groups. Roles and projects have no status; a role appears once a profile picks one of its confirmed bullets. Once you've checked everything, `rb confirm --all` confirms all drafts in one go (it lists them and asks first).
3. **`profiles/public.yaml`**: list the ids you want on your general resume.

Save, and the app syncs within a few seconds. If something's wrong, `SYNC-STATUS.md` (and the website's home page) says which file, which line and how to fix it.

See a first draft at any time, even before you've confirmed anything:

```sh
rb render public --preview
```

The PDF lands in `output/<your-name>/public/`.

## 4. Add a job

```sh
rb new-job https://careers.example.com/jobs/1234
```

It reads the posting from the link, shows what it found (title, location, salary, dates and so on) and the company it matched, and asks once before creating `data/<your-name>/jobs/<job-id>/` with `posting.md` and `application.yaml` filled in. If the site needs a sign-in, copy the posting into a file in `data/<your-name>/intake/` and run `rb new-job --file intake/<name>.txt <link>` instead. Then save why you want the job, in your own words: `rb set <job-id> --interest-notes "..."` (or write it under `interest_notes` in `application.yaml`). Letters use only these notes for motivation.

No link? `rb new-job --title "Platform Engineer"` (without a link) asks for the company and creates the folder; paste the job description into `posting.md` yourself.

## 5. Tailor it

**With an AI assistant.** Step-by-step setup for Claude, Gemini, ChatGPT and Copilot is in [AI-AGENTS.md](AI-AGENTS.md). Two options:

- *Assistants that support MCP* (Claude Code, Claude Desktop, Gemini CLI, Codex CLI, Copilot in VS Code) connect to resume-builder directly. Set yours up once with section 2 of [AI-AGENTS.md](AI-AGENTS.md); you don't run anything yourself, the assistant starts resume-builder when it needs it. Then, in the assistant's chat, type a request such as: "Use the resume-builder tools to tailor job `2026-11-02-northwind-labs-platform-engineering`" (use your own job id, from `rb list`). The assistant reads the job's brief, writes the tailored profile and fit report, and tells you the gaps.
- *Chat in the browser* (claude.ai, Gemini, ChatGPT, Microsoft Copilot): paste the output of `rb brief <job-id>`, then pipe each block it gives back into the app, e.g. `pbpaste | rb propose <job-id> profile` (macOS) or `Get-Clipboard | rb propose <job-id> profile` (Windows PowerShell).

Either way, the drafts are checked and saved as `profile.yaml` and `fit-report.md` in the job folder for you to review.

**By hand**: copy `profiles/public.yaml` to `jobs/<job-id>/profile.yaml` and reorder or trim the ids.

## 6. Render, review, apply

```sh
rb render <job-id>                       # tailored resume PDF in output/<your-name>/<job-id>/
rb approve <job-id> answer               # if you have a "why here" answer and you're happy with it
rb snapshots <job-id>                    # ids of what you rendered
rb event <job-id> --stage applied --documents <resume-snapshot-id> <answer-snapshot-id>
```

Recording the snapshot ids you sent means you can rebuild exactly those documents later, and the dashboard can tell which resume versions get interviews.

## 7. Keep tracking

As things happen, add events at the end of `events:` in `application.yaml`, or use `rb event <job-id> --type message_received --notes "Recruiter replied"`. The website shows each application's history, upcoming actions, stale applications and your search statistics.

Not sure what to run next? Each job's page on the website starts with **Next steps**, the commands for where that job is, filled in with its ids and a **Copy** button. The **Commands** page has every everyday command the same way, and `rb next <job-id>` prints a job's next steps in the terminal.

Stop the app with `docker compose down`. Your data stays in `data/`, the database volume and `backups/`.
