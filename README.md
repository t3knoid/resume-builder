# resume-builder

Keep one honest, evidence-backed record of your career, and build a tailored resume, cover letter or short application answer for each job from it, without the AI inventing claims. Track every application from first interest to outcome, and rebuild any document you ever sent, exactly.

- **[Quick start](docs/QUICKSTART.md)**: running in about 15 minutes.
- **[User manual](docs/USER-MANUAL.md)**: everything else.
- **[AI assistants](docs/AI-AGENTS.md)**: setup for Claude, Gemini, ChatGPT and Copilot.
- **[Product requirements](docs/PRD.md)**: the design and the decisions behind it.
- **[Architecture](docs/ARCHITECTURE.md)**: layers and extension points, for contributors and plugin authors.
- **[Changelog](CHANGELOG.md)**: what changed in each version.

Runs locally in Docker. Your data stays in your own folder (`data/<you>/`), a local SQLite database and your backups. The website and API listen only on `127.0.0.1`.

```sh
sh setup.sh             # Windows: .\setup.bat. The first run creates .env and stops:
                        # set RB_DEFAULT_USER and your email there, then run it again
open http://localhost:8000
```

The setup script builds and starts the app (`docker compose up -d --build`) and installs the `rb` command.

A complete fictional example user lives in [`examples/sample-user/`](examples/sample-user/).

## License and contributing

resume-builder is free software under the [GNU Affero General Public License v3.0 or later](LICENSE). Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) (commits need a DCO sign-off).
