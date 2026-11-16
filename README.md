# resume-builder

Keep one honest, evidence-backed record of your career, and build a tailored resume, cover letter or short application answer for each job from it, without the AI inventing claims. Track every application from first interest to outcome, and rebuild any document you ever sent, exactly.

## Works with your AI assistant (MCP server)

resume-builder is an **MCP server**. Connect Claude Code, Claude Desktop, Gemini CLI, Codex CLI or GitHub Copilot in VS Code, and your assistant gets resume-builder's tools. It reads a job's brief (`agent_brief`), picks bullets for a tailored resume (`profile_write`), and drafts fit reports, cover letters and application answers (`document_propose`). It can also propose new jobs and tracker events.

The honesty rules are enforced in code, not in a prompt. The agent can only select from your confirmed bullets, every claim in a letter must cite them, and everything it writes is a draft until you approve it. It can never confirm, approve or publish.

Connect it either way:

- **A command (stdio)**: the assistant starts `rb mcp` itself.
- **A URL (MCP over HTTP)**: `http://localhost:8000/mcp/<you>`, with an agent token.

Chat assistants without MCP (claude.ai, ChatGPT, Gemini in the browser) work through copy and paste. See **[AI assistants](docs/AI-AGENTS.md#1-three-ways-to-connect)**.

## Documentation

- **[Quick start](docs/QUICKSTART.md)**: running in about 15 minutes.
- **[User manual](docs/USER-MANUAL.md)**: everything else.
- **[AI assistants](docs/AI-AGENTS.md)**: setup for Claude, Gemini, ChatGPT and Copilot.
- **[Product requirements](docs/PRD.md)**: the design and the decisions behind it.
- **[Architecture](docs/ARCHITECTURE.md)**: layers and extension points, for contributors and plugin authors.
- **[Changelog](CHANGELOG.md)**: what changed in each version.

## Getting started

Runs locally in Docker. Your data stays in your own folder (`data/<you>/`), a local SQLite database and your backups. The website and API listen only on `127.0.0.1`.

```sh
sh setup.sh             # Windows: .\setup.bat. The first run creates .env and stops:
                        # set RB_DEFAULT_USER and your email there, then run it again
open http://localhost:8000   # Windows: start http://localhost:8000
```

The setup script builds and starts the app (`docker compose up -d --build`) and installs the `rb` command.

A complete fictional example user lives in [`examples/sample-user/`](examples/sample-user/).

## License and contributing

resume-builder is free software under the [GNU Affero General Public License v3.0 or later](LICENSE). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) (commits need a DCO sign-off).
