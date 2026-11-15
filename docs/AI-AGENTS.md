# Using resume-builder with an AI agent

resume-builder has no AI model of its own. You bring your own assistant (Claude, Gemini, ChatGPT, Copilot or another), and it does the tailoring: it reads the job posting and your confirmed content, then proposes a tailored resume (a *profile*), a fit report and, if you ask, a cover letter or a "Why do you want to work here?" answer.

Whatever the assistant, it works as the **agent**: it can only propose drafts, and every draft goes through the same honesty checks (section 9 of the [user manual](USER-MANUAL.md)). You review and approve.

## Contents

1. [Three ways to connect](#1-three-ways-to-connect)
2. [Before you start](#2-before-you-start)
3. [Claude](#3-claude): Claude Code, Claude Desktop, claude.ai
4. [Gemini](#4-gemini): Gemini CLI, Gemini in the browser
5. [ChatGPT](#5-chatgpt): Codex CLI, ChatGPT in the browser or app
6. [Copilot](#6-copilot): GitHub Copilot in VS Code, Copilot CLI, Microsoft Copilot
7. [Other tools](#7-other-tools)
8. [A typical session](#8-a-typical-session)
9. [Reviewing what the agent did](#9-reviewing-what-the-agent-did)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Three ways to connect

| Way | Works with | What it's like |
| --- | --- | --- |
| **MCP tools** (recommended) | Claude Code, Claude Desktop, Gemini CLI, Codex CLI, GitHub Copilot in VS Code, Copilot CLI, Cursor and other MCP clients | The assistant calls resume-builder directly: reads the brief, saves drafts, sees problems and fixes them itself. |
| **Copy and paste** | Any chat assistant: claude.ai, gemini.google.com, chatgpt.com, Microsoft Copilot | You paste a brief in; the assistant replies with blocks; you pipe each block into `rb propose`. |
| **HTTP API** | Your own scripts | `POST http://localhost:8000/api/<you>/<operation>` with an agent token. Use `localhost` or `127.0.0.1`: other host names are refused. |

**MCP** (Model Context Protocol) is a standard way for AI tools to use local tools. resume-builder includes an MCP server, `rb mcp`. Your assistant starts it on demand through Docker; there's nothing extra to install.

**Why browser assistants can't connect directly:** web connectors in ChatGPT, Gemini, claude.ai and Microsoft Copilot need a server reachable from the internet. resume-builder deliberately listens only on your own computer, so for those you use copy and paste.

### What the agent can and can't do

| Can | Can't |
| --- | --- |
| Read your confirmed content, postings, applications, snapshots, dashboard | Confirm content (`draft` → `confirmed`) |
| Write tailored profiles and fit reports | Approve cover letters or answers |
| Draft cover letters and answers (strict checks) | Record events, only propose them for you to confirm |
| Propose new bullets (always saved as drafts) | Add, rename or merge companies |
| Propose events, e.g. from a recruiter email you paste | Sync, back up, restore, publish, delete or pin snapshots |
| Propose a new job from a posting link or text | Create a job, only propose it for you to confirm |

Everything the agent does is in the audit log (`rb audit`), marked `agent`, with the channel it used (`mcp`, `cli` for pasted drafts, or `api`).

### Do I need a paid AI plan?

**No.** resume-builder itself is free and has no AI built in; it never charges you or calls an AI service. Whether you pay anything depends only on the assistant you choose, and **copy and paste works with the free version of any chat assistant** (claude.ai, Gemini, ChatGPT, Microsoft Copilot).

The assistants that use resume-builder's tools directly (MCP) differ. As of November 2026:

| Assistant | Free plan? | Notes |
| --- | --- | --- |
| claude.ai, Gemini, ChatGPT, Microsoft Copilot (copy and paste) | Yes | Free plans have daily or hourly limits; a long tailoring session may hit them. |
| Claude Desktop (MCP) | Yes | Local MCP servers, which is how resume-builder connects, work on the free plan, within its usage limits. |
| Claude Code | **No** | Needs a paid Claude plan (Pro, Max, Team or Enterprise) or an Anthropic API account billed per use. The program installs for free, but won't start a session without one. |
| GitHub Copilot in VS Code, Copilot CLI | Yes, limited | Copilot Free includes them, with a monthly allowance; paid Copilot plans raise it. |
| Codex CLI | Yes | Codex is included on every ChatGPT plan, including Free and Go: sign in with your ChatGPT account. Free plans have lower usage limits; paid plans or an OpenAI API key (billed per use) give more. Codex Cloud isn't on Free or Go, but resume-builder doesn't need it. See OpenAI's help article "Using Codex with your ChatGPT plan". |
| Gemini CLI | Yes | Signing in with a personal Google account is free, up to 1,000 requests a day. A free Gemini API key also works, with 250 requests a day on the Flash model only. Google AI Pro and Ultra raise the limit; Google AI Plus doesn't include Gemini CLI. Google's [Gemini CLI quotas and pricing](https://geminicli.com/docs/resources/quota-and-pricing) page has the current numbers. |
| Your own scripts (HTTP API) | Depends | Whatever model or service your script calls. |

Plans, limits and prices change often; the provider's own pricing page is the authority. If you just want to try resume-builder, start with copy and paste on a free chat assistant, or with Claude Desktop, Gemini CLI or Codex CLI, which connect directly on a free plan.

## 2. Before you start

1. resume-builder is installed and has run at least once (`docker compose up -d --build`; see the [quick start](QUICKSTART.md)). Building first matters: an MCP client can't wait for a build.
2. You've added a job with its posting (`rb new-job <link to the posting>`, or `rb new-job` and then pasting the posting into `posting.md`), and written your `interest_notes` in its `application.yaml`.
3. Some content in `base.yaml` is `confirmed`. The agent can only use confirmed items.

**Checking it worked:** after setting up your assistant, ask it to call `agent_brief` for a job (or to list your applications). The **AI agent** card on the tracker's home page, and `rb status`, then say "Connected" with the channel (MCP or HTTP API) and when; for MCP it also shows your assistant's name if it sends one. If it still says "No AI agent has connected yet", the assistant hasn't reached resume-builder: see Troubleshooting (section 10).

### The MCP command

Every MCP setup below uses the same command, with your own path to the resume-builder folder:

```text
docker compose -f /path/to/resume-builder/compose.yaml run --rm -T app rb mcp
```

- `-f` with a **full path** lets the assistant start it from any folder.
- `-T` is required: MCP talks over standard input and output, which must not be a terminal.
- With more than one user folder, add your name: `... app rb --user <you> mcp`.
- **Desktop apps** (Claude Desktop, VS Code launched from the Dock or Start menu) may not find `docker` on their PATH. Use the full path to the Docker program: run `which docker` (macOS/Linux; usually `/usr/local/bin/docker` on macOS, `/usr/bin/docker` on Linux) or `where docker` (Windows; usually `C:\Program Files\Docker\Docker\resources\bin\docker.exe`).
- In JSON on Windows, double every backslash: `"C:\\Users\\you\\resume-builder\\compose.yaml"`.

Test it by hand. It should print a "ready" line, then wait. Press Ctrl+C to stop.

```sh
docker compose -f /path/to/resume-builder/compose.yaml run --rm -T app rb mcp
# resume-builder MCP server for `you` ready (27 tools).
```

### Install your assistant and check it's on your PATH

The terminal assistants (Claude Code, Gemini CLI, Codex CLI, GitHub Copilot CLI) are separate programs you install first. Each is started by a command; that command must be on your PATH, or the steps below fail with "command not found" (macOS, Linux) or "is not recognized" (Windows).

| Assistant | Command | Install (see its own documentation for the current steps) |
| --- | --- | --- |
| Claude Code | `claude` | Anthropic's installer, or `npm install -g @anthropic-ai/claude-code` (section 3) |
| Gemini CLI | `gemini` | `npm install -g @google/gemini-cli` |
| Codex CLI | `codex` | `npm install -g @openai/codex` |
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` |

The `npm` lines need [Node.js](https://nodejs.org) (a current LTS version). Package names and installers change; if one fails, follow the assistant's own setup guide.

Then **open a new terminal** and check, for example:

```sh
gemini --version
```

If the command isn't found, the installer put it in a folder that isn't on your PATH. For npm installs, `npm prefix -g` shows that folder (on macOS and Linux add `/bin` to it; on Windows it's the folder itself). Add it to your PATH, open a new terminal and check again.

Claude Desktop and VS Code are ordinary apps: install them as usual; there's no command to check.

### On Windows

The examples in this guide use macOS and Linux syntax. In **Windows PowerShell**:

- **Working folder:** `mkdir $HOME\resume-agent; cd $HOME\resume-agent` (instead of `mkdir -p ~/resume-agent && cd ~/resume-agent`).
- **Paths:** use your Windows path, for example `C:\Users\you\resume-builder\compose.yaml`. In JSON files, double every backslash: `"C:\\Users\\you\\resume-builder\\compose.yaml"`. In TOML files (Codex), use single quotes, which take the path as written: `'C:\Users\you\resume-builder\compose.yaml'`.
- **Saving instruction files:** Windows PowerShell 5.1 writes `>` output as UTF-16, which assistants may not read. Use `Out-File` instead:

  ```powershell
  docker compose -f C:\Users\you\resume-builder\compose.yaml run --rm app rb agent-instructions --for mcp | Out-File -Encoding utf8 GEMINI.md
  ```

  (PowerShell 7 writes UTF-8 with `>`, so either form works there.)
- **Folders for instruction files:** `mkdir .github` (instead of `mkdir -p .github`).

### Keep coding agents out of your data folder

Coding assistants (Claude Code, Gemini CLI, Codex CLI, Copilot agent mode) can also edit files. If one edits files in `data/<you>/` directly, the change syncs as **yours**, skipping the agent's checks. So:

- start the assistant in a **separate, empty folder** (for example `~/resume-agent`), not inside resume-builder or your data folder; the MCP command's full `-f` path makes this work;
- and tell it to use only the resume-builder tools (the standing instructions below say so).

### Standing instructions

`rb agent-instructions` prints the rules every assistant should follow, in three versions:

```sh
rb agent-instructions --for mcp     # for MCP-connected assistants
rb agent-instructions --for chat    # for copy-and-paste (includes the reply format)
rb agent-instructions --for api     # for scripts using the HTTP API
```

MCP assistants also receive these automatically when they connect, so adding them to the assistant's own instructions file is optional, but it helps keep long sessions on track.

### The copy-and-paste helpers

These use the `rb` command (install it once with `scripts/install-rb.sh`, or `scripts\install-rb.cmd` on Windows; see the user manual). Text piped into `rb` goes straight through to the app.

Paste from the clipboard into resume-builder:

| System | Command |
| --- | --- |
| macOS | `pbpaste \| rb propose <job-id> profile` |
| Linux (X11) | `xclip -o -selection clipboard \| rb propose <job-id> profile` |
| Linux (Wayland) | `wl-paste \| rb propose <job-id> profile` |
| Windows (PowerShell) | `Get-Clipboard \| rb propose <job-id> profile` |

The last word is what you pasted: `profile`, `fit_report`, `cover_letter` or `answer`. A surrounding
```` ```yaml ```` fence is fine. `rb propose` submits as the **agent**, so pasted drafts get exactly
the same checks as an MCP-connected one. If something fails, it prints the problems; paste them back
to the assistant.

---

## 3. Claude

### Claude Code (recommended)

Claude Code is Anthropic's coding agent. You run it in a terminal, as the `claude` command, and it supports MCP directly. These steps use only a terminal. It needs a paid Claude plan or an Anthropic API account ([Do I need a paid AI plan?](#do-i-need-a-paid-ai-plan)).

1. Install Claude Code, if you haven't, following Anthropic's Claude Code setup guide. At the time of writing, the installer is one line:

   ```sh
   curl -fsSL https://claude.ai/install.sh | bash          # macOS, Linux
   ```

   ```powershell
   irm https://claude.ai/install.ps1 | iex                 # Windows PowerShell
   ```

   (With Node.js installed, `npm install -g @anthropic-ai/claude-code` works too.) Then **open a new terminal** and check that the command is found:

   ```sh
   claude --version
   ```

   If it says "command not found" (macOS, Linux) or "is not recognized" (Windows), the installer put `claude` in a folder that isn't on your PATH yet. It prints that folder when it finishes (often `~/.local/bin`, or `%USERPROFILE%\.local\bin` on Windows). Add the folder to your PATH, or open a new terminal if the installer already did, and try again. Anthropic's setup guide covers anything else the installer asks for on your system.

2. Make a working folder for job-search sessions, outside resume-builder, and go into it:

   ```sh
   mkdir -p ~/resume-agent && cd ~/resume-agent          # macOS, Linux
   ```

   ```powershell
   mkdir $HOME\resume-agent; cd $HOME\resume-agent       # Windows PowerShell
   ```

3. Add the server, in that folder (one line; use your own path):

   ```sh
   claude mcp add resume-builder -- docker compose -f /path/to/resume-builder/compose.yaml run --rm -T app rb mcp
   ```

   ```powershell
   claude mcp add resume-builder -- docker compose -f C:\Users\you\resume-builder\compose.yaml run --rm -T app rb mcp
   ```

   This registers it for this folder. Add `--scope user` before the name to make it available everywhere, or `--scope project` to save it in a `.mcp.json` file in this folder. With more than one user folder, add `--user <you>` before `mcp`.

   **No `claude` command?** (for example, you use Claude Code only inside an IDE): create the file `.mcp.json` in the working folder yourself; it does the same:

   ```json
   {
     "mcpServers": {
       "resume-builder": {
         "command": "docker",
         "args": ["compose", "-f", "/path/to/resume-builder/compose.yaml", "run", "--rm", "-T", "app", "rb", "mcp"]
       }
     }
   }
   ```

   On Windows, write the path with doubled backslashes, for example `"C:\\Users\\you\\resume-builder\\compose.yaml"`. If `docker` isn't found, put its full path in `"command"` (see [The MCP command](#the-mcp-command)). Claude Code asks you to approve the project's MCP server the first time.

4. Optional: save the standing instructions where Claude Code reads them:

   ```sh
   docker compose -f /path/to/resume-builder/compose.yaml run --rm app rb agent-instructions --for mcp > CLAUDE.md
   ```

   In Windows PowerShell 5.1, write it as `... rb agent-instructions --for mcp | Out-File -Encoding utf8 CLAUDE.md` (see [On Windows](#on-windows)).

5. Start Claude Code in that folder (`claude`) and check the connection with `/mcp`: resume-builder should be listed as connected, with its tools. The **AI agent** card on the tracker's home page says "Connected through MCP" once it has used a tool.

6. Ask, for example:

   > Use the resume-builder tools to tailor job 2026-11-13-acme-corp-platform. Read the agent brief,
   > write the profile and the fit report, and tell me what the gaps are.

   Claude Code asks permission the first time it uses each tool. Allow the resume-builder ones.

### Claude Desktop

1. Open Claude Desktop → **Settings** → **Developer** → **Edit Config**. This opens
   `claude_desktop_config.json`:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

2. Add the server. Use the full path to `docker` (desktop apps often can't find it otherwise):

   ```json
   {
     "mcpServers": {
       "resume-builder": {
         "command": "/usr/local/bin/docker",
         "args": ["compose", "-f", "/Users/you/resume-builder/compose.yaml", "run", "--rm", "-T", "app", "rb", "mcp"]
       }
     }
   }
   ```

   If the file already has `mcpServers`, add `"resume-builder": {...}` inside it.

3. Quit Claude Desktop completely and reopen it. resume-builder's tools appear in the tools
   (connectors) menu in the message box.

4. Optional: create a **Project** called "Job search" and paste `rb agent-instructions --for mcp` into
   its instructions.

5. Ask Claude to tailor a job by its id, as above. Approve the tool calls when asked.

### claude.ai (browser or mobile), copy and paste

1. Create a **Project** (for example "Job search"). Paste the output of
   `rb agent-instructions --for chat` into the project's instructions.
2. For each job, start a chat in the project and paste the brief:

   ```sh
   rb brief <job-id> | pbcopy        # macOS; or run `rb brief <job-id>` and copy the output
   ```

3. Ask: "Tailor this. Give me the profile.yaml and fit-report.md blocks." Add "and a why-here
   answer" or "and a cover letter" if you want them.
4. Copy each block Claude returns and pipe it in:

   ```sh
   pbpaste | rb propose <job-id> profile
   pbpaste | rb propose <job-id> fit_report
   ```

5. If `rb propose` reports problems, paste them back into the chat and ask Claude to fix exactly those.

---

## 4. Gemini

### Gemini CLI (recommended)

Gemini CLI is Google's open-source agent for the terminal. It supports MCP servers configured in a
`settings.json` file.

1. Install Gemini CLI and check that `gemini --version` works in a new terminal ([Install your assistant](#install-your-assistant-and-check-its-on-your-path)).

2. Make a working folder outside resume-builder: `mkdir -p ~/resume-agent && cd ~/resume-agent` (Windows: [On Windows](#on-windows)).

3. Create `.gemini/settings.json` in that folder (or edit `~/.gemini/settings.json` to make it
   available everywhere):

   ```json
   {
     "mcpServers": {
       "resume-builder": {
         "command": "docker",
         "args": ["compose", "-f", "/path/to/resume-builder/compose.yaml", "run", "--rm", "-T", "app", "rb", "mcp"],
         "timeout": 60000
       }
     }
   }
   ```

   `timeout` (milliseconds) gives Docker time to start the container on the first call. On Windows, double the backslashes in the path.

4. Optional: save the standing instructions as `GEMINI.md`, which Gemini CLI reads as context (Windows PowerShell 5.1: use `Out-File -Encoding utf8`, see [On Windows](#on-windows)):

   ```sh
   docker compose -f /path/to/resume-builder/compose.yaml run --rm app rb agent-instructions --for mcp > GEMINI.md
   ```

5. Start `gemini` in that folder and run `/mcp`: resume-builder should be listed with its tools.

6. Ask:

   > Use the resume-builder tools: get the agent brief for job 2026-11-13-acme-corp-platform, then
   > write the profile and the fit report.

   Gemini CLI asks before running each tool. Allow resume-builder's tools.

### Gemini in the browser (gemini.google.com), copy and paste

1. If your account has **Gems**, create one (for example "Resume tailor") and paste
   `rb agent-instructions --for chat` as its instructions. Otherwise paste the instructions at the
   start of each chat.
2. Paste `rb brief <job-id>` into a chat with the Gem.
3. Ask for the profile.yaml and fit-report.md blocks (plus a letter or answer if you want one).
4. Copy each block and pipe it in: `pbpaste | rb propose <job-id> profile`, and so on.
5. Paste any problems back and ask Gemini to fix exactly those.

Gemini Code Assist in VS Code can also use MCP servers in its agent mode, using the same
`settings.json` format as Gemini CLI. Check Google's current documentation for your version.

---

## 5. ChatGPT

### ChatGPT in the browser or desktop app, copy and paste

ChatGPT's own connectors need a server on the internet, so use copy and paste:

1. Create a **Project** (for example "Job search") and paste `rb agent-instructions --for chat` into
   its instructions. A custom GPT with the same instructions works too.
2. For each job, start a chat in the project and paste `rb brief <job-id>`.
3. Ask for the profile.yaml and fit-report.md blocks, plus a cover letter or why-here answer if you
   want one.
4. Copy each block and pipe it in:

   ```sh
   pbpaste | rb propose <job-id> profile
   pbpaste | rb propose <job-id> fit_report
   pbpaste | rb propose <job-id> answer
   ```

5. Paste any problems back and ask ChatGPT to fix exactly those.

### Codex CLI (MCP)

Codex CLI is OpenAI's coding agent for the terminal; you can sign in with your ChatGPT account. It
supports MCP servers configured in `~/.codex/config.toml`.

1. Install Codex CLI and check that `codex --version` works in a new terminal ([Install your assistant](#install-your-assistant-and-check-its-on-your-path)).

2. Make a working folder outside resume-builder: `mkdir -p ~/resume-agent && cd ~/resume-agent` (Windows: [On Windows](#on-windows)).

3. Add to `~/.codex/config.toml` (on Windows, `%USERPROFILE%\.codex\config.toml`):

   ```toml
   [mcp_servers.resume-builder]
   command = "docker"
   args = ["compose", "-f", "/path/to/resume-builder/compose.yaml", "run", "--rm", "-T", "app", "rb", "mcp"]
   ```

   On Windows, put the path in single quotes: `'C:\Users\you\resume-builder\compose.yaml'`.

4. Optional: save the standing instructions as `AGENTS.md`, which Codex reads (Windows PowerShell 5.1: use `Out-File -Encoding utf8`, see [On Windows](#on-windows)):

   ```sh
   docker compose -f /path/to/resume-builder/compose.yaml run --rm app rb agent-instructions --for mcp > AGENTS.md
   ```

5. Start `codex` in that folder. Run `/mcp` to confirm resume-builder and its tools are listed.

6. Ask Codex to tailor a job by its id using the resume-builder tools, as in the other examples.

If Codex reports a timeout starting the server, start the container once by hand first (the command
in section 2) so the image is ready. If it still times out, increase the MCP startup timeout described
in Codex's documentation.

---

## 6. Copilot

### GitHub Copilot in VS Code, agent mode (recommended)

VS Code's Copilot Chat can use MCP tools in **Agent** mode.

1. Open a folder for job-search sessions that is **not** resume-builder or your data folder (for
   example `~/resume-agent`): File → Open Folder.

2. Create `.vscode/mcp.json` in it:

   ```json
   {
     "servers": {
       "resume-builder": {
         "type": "stdio",
         "command": "docker",
         "args": ["compose", "-f", "/path/to/resume-builder/compose.yaml", "run", "--rm", "-T", "app", "rb", "mcp"]
       }
     }
   }
   ```

   (Or run **MCP: Add Server** from the Command Palette, choose a command-based (stdio) server, and
   enter the same command.) If VS Code can't find `docker`, use its full path (section 2).

3. In `mcp.json`, click **Start** above the server name, or let VS Code start it when needed.

4. Optional: save the standing instructions as `.github/copilot-instructions.md`, which Copilot uses
   for every chat in this folder:

   ```sh
   mkdir -p .github
   docker compose -f /path/to/resume-builder/compose.yaml run --rm app rb agent-instructions --for mcp > .github/copilot-instructions.md
   ```

   In Windows PowerShell use `mkdir .github` and `| Out-File -Encoding utf8 .github\copilot-instructions.md` (see [On Windows](#on-windows)).

5. Open Copilot Chat, switch the mode to **Agent**, and open the tools picker: make sure the
   resume-builder tools are ticked.

6. Ask Copilot to tailor a job by its id using the resume-builder tools. Approve each tool call when
   asked.

Your organization's Copilot policy may switch MCP off. If the tools picker shows no MCP servers, ask
your administrator.

### GitHub Copilot CLI

GitHub Copilot CLI (`copilot`) can use local MCP servers too. Install it first and check that `copilot --version` works in a new terminal ([Install your assistant](#install-your-assistant-and-check-its-on-your-path)). Then, in a Copilot CLI session, run
`/mcp add`, choose a local (stdio) server, name it `resume-builder`, and enter the same command:
`docker compose -f /path/to/resume-builder/compose.yaml run --rm -T app rb mcp`. Start it from an
empty working folder, as with the other coding agents. Menus vary between versions; GitHub's
documentation for Copilot CLI has the current steps.

### Microsoft Copilot (copilot.microsoft.com, Microsoft 365 Copilot), copy and paste

1. Start a chat and paste `rb agent-instructions --for chat`, then `rb brief <job-id>`. (In
   Microsoft 365 Copilot you can save the instructions in a custom agent if your organization allows it.)
2. Ask for the profile.yaml and fit-report.md blocks.
3. Pipe each block in with `rb propose` (on Windows: `Get-Clipboard | rb propose <job-id> profile`).
4. Paste any problems back and ask Copilot to fix exactly those.

---

## 7. Other tools

**Any MCP client** (Cursor, Windsurf, Zed, Continue, and others) can use the command in section 2.
Most use the same JSON shape as Claude Desktop: `command` plus `args`.

**Your own scripts** can use the HTTP API:

```sh
rb token --actor agent          # shown once; replaces any previous agent token
curl -s http://localhost:8000/api/<you>/agent_brief \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"job_id": "<job-id>", "mode": "api"}'
```

All operations and their input formats are documented at <http://localhost:8000/docs>. Problems come
back as HTTP 422 with a `problems` list.

---

## 8. A typical session

1. **You:** `rb new-job <link to the posting>` (it reads the posting and fills in the job), then write
   `interest_notes`. Or, with an MCP or API agent, paste the link into the chat and say "start a job
   for this": the agent proposes it, and you review it with `rb confirm-job <n>` (see below).
2. **Agent:** reads the brief: rules, posting, your interest notes, and every confirmed bullet with
   its id, tags, metric and limits.
3. **Agent:** saves `profile.yaml` (ids only) and `fit-report.md`. If it breaks a rule, for example
   by citing a bullet that doesn't exist or claiming a keyword you don't have, the app says exactly
   what's wrong and the agent fixes it.
4. **You:** read `jobs/<job-id>/fit-report.md`. For each gap, decide: real gap, or missing
   information? Add missing information to `base.yaml` as a draft, confirm it, and ask the agent to
   update the profile.
5. **Agent (if asked):** drafts a cover letter or why-here answer, with every claim cited.
6. **You:** edit if you like, then `rb approve <job-id> answer` / `cover_letter`, then
   `rb render <job-id>`.
7. **Later:** paste a recruiter's email to the agent. It proposes an event; you confirm it with
   `rb confirm-event <n>`.

Good prompts are short and specific:

- "Tailor job `<job-id>`: brief, profile, fit report. List the gaps as questions for me."
- "Draft a why-here answer for `<job-id>` under 100 words. Use my interest notes for the first sentence."
- "This recruiter email arrived for `<job-id>`; propose the right event: …"
- "Compare my last two resume snapshots for `<job-id>` and tell me what changed."
- "Start a job for this posting: <link>." (MCP or API agents)

### Paste the link, then confirm

With an MCP or API agent you can hand it a posting link (or the posting text) and let it propose the job:

1. **You:** "Start a job for this posting: https://careers.example.com/jobs/1234".
2. **Agent:** calls `job_intake_propose` with the link. The app fetches the page itself, with the same safety checks as `rb new-job <url>`. The agent may add values it read from the posting, each with the exact passage that states it; the app rejects any value the posting doesn't state. Nothing is created yet.
3. **You:** `rb job-drafts` lists what's waiting (so do `rb status` and `SYNC-STATUS.md`). `rb confirm-job <n>` shows the same preview and asks the same questions as `rb new-job <url>`: company, duplicates, any value to change. Or `rb discard-job <n>`.

The agent can't create the job, fill in your `interest_notes`, or add companies. Chat-only assistants can't propose jobs; run `rb new-job <link>` yourself.

## 9. Reviewing what the agent did

- **Files:** the agent's drafts are written to your job folder (`profile.yaml`, `fit-report.md`,
  `cover-letter.md`, `answers/why-here.md`) so you can read and edit them. Your edits sync back as yours.
- **Website:** each job page shows documents and their status, and the timeline shows proposed
  events waiting for you. **Proposed jobs** lists the jobs the agent proposed, with every value and
  the passage it came from, and the command to confirm or discard each one. Sign in on the site (`rb web-login`) to confirm or discard
  proposed jobs and events there with a button.
- **Audit log:** `rb audit` lists every action with who did it (`agent`, `owner`, `system`) and how.
- **Nothing is final until you approve it.** Letters and answers need `rb approve`; events need
  `rb confirm-event`; proposed jobs need `rb confirm-job`; new bullets need `status: confirmed` (or `rb confirm <id>`).

## 10. Troubleshooting

| Problem | Fix |
| --- | --- |
| The home page says "No AI agent has connected yet" after setup | The assistant hasn't reached resume-builder. For MCP, run the MCP command from section 2 by hand and look for "ready"; then restart the assistant. For the HTTP API, check the token and that the address is `localhost` or `127.0.0.1`. |
| The home page says the assistant used a token that's "no longer valid" | You replaced the token with `rb token`. Issue a new one with `rb token --actor agent` and update your assistant's settings. |
| The assistant says the server failed to start or timed out | Run the MCP command from section 2 by hand. If it builds the image, wait for that to finish and try again. Increase the client's timeout if it has one. |
| `claude`, `gemini`, `codex` or `copilot`: "command not found" or "is not recognized" | The assistant isn't installed, or its folder isn't on your PATH. See [Install your assistant](#install-your-assistant-and-check-its-on-your-path), then open a new terminal. |
| The assistant ignores the standing instructions you saved (Windows) | Windows PowerShell 5.1 saved the file as UTF-16. Save it again with `\| Out-File -Encoding utf8 <file>` ([On Windows](#on-windows)). |
| "command not found: docker" in a desktop app | Use the full path to `docker` as the `command` (section 2). |
| "Which user?" | You have several user folders: add `--user <you>` after `rb` in the MCP command. |
| The tools list is empty or missing tools | The agent only sees what it's allowed to do; approving, confirming and recording events are yours (section 1). |
| The agent changed files instead of using tools | Start it from an empty folder outside resume-builder, and add the standing instructions. |
| `rb propose` says "Nothing on stdin" | Pipe the text in: `pbpaste \| rb propose <job-id> profile` (Windows: `Get-Clipboard \| rb propose …`). Copy the block first. |
| "the input device is not a TTY" | Your Docker Compose is too old to detect piped input. Update Docker, or run `docker compose run --rm -T app rb propose …` from the `resume-builder` folder. |
| A pasted profile fails with "not a known field" | The assistant invented a field. Paste the problems back; the reply format in `rb agent-instructions --for chat` lists the allowed fields. |
| "Claim without a citation" or "number isn't in the cited bullets" | The checks are working: the draft claims something your content doesn't support. Ask the assistant to fix exactly those sentences. |
| The agent's token stopped working (HTTP API) | `rb token --actor agent` replaced it. Update your script. MCP and `rb propose` don't use tokens. |
