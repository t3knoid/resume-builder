# Changelog

Notable changes to resume-builder, newest first. Requirement and decision ids (such as R7.5, B9a, D21) and section numbers refer to the [product requirements](docs/PRD.md).

## Unreleased

- Job intake groundwork (#17): captured job postings are stored in the database (migration 0002, R13.10, D29). Captures are append-only, enforced by the schema; every capture is kept. Migrations can now contain triggers.
- One-step setup: `setup.sh` (macOS, Linux) and `setup.bat` / `setup.ps1` (Windows) create `.env` on the first run, then build and start the app and install the `rb` command.
- `rb` without Docker commands: `scripts/install-rb.sh` (macOS, Linux) and `scripts/install-rb.cmd` (Windows) install an `rb` command that runs `rb` in the container from any folder. On Windows it is added to the user PATH without changing anything else in it, and `-Uninstall` restores the PATH exactly.
- PRD: added section 8.13, job intake from a posting URL (default) or pasted text, with decisions D24–D29 (approved November 15, 2026) and the `application:propose` permission for the agent (B2, B7). Not yet implemented.
- Piped input no longer needs a separate `rbi` alias: `rb propose` accepts piped text directly, and ignores the byte-order mark Windows PowerShell 5.1 adds to piped text.

## 0.2.0 (November 14, 2026)

### Extension points

- Templates and migrations moved into the package (`src/resume_builder/templates/`, `src/resume_builder/migrations/`), so an installed copy works without a source checkout.
- Added the interfaces behind A1–A5 and B1–B8 as public extension points:
  - an app factory (`create_app`, `build_app`);
  - a `Storage` interface, with SQLite as the only backend;
  - `tenant_id` and `user_id` in the request context;
  - change notifications that make YAML sync an input adapter over the services;
  - `SecretStore` and `FileStore`;
  - website template overrides;
  - operation plugins, passed explicitly or found through the `resume_builder.plugins` entry point group.

  See [ARCHITECTURE.md](docs/ARCHITECTURE.md).
- The project is licensed AGPL-3.0-or-later; contributions require a DCO sign-off ([CONTRIBUTING.md](CONTRIBUTING.md)).

## 0.1.0 (November 13, 2026)

### Agent integrations

- Added the MCP server (`rb mcp`), `rb propose` for pasted drafts, and `rb agent-instructions`. The last keeps one shared copy of the agent rules, which is also sent as the MCP server's instructions. The MCP server and `rb propose` always act as the `agent` actor, so a pasted or tool-submitted draft gets the agent's strict checks and is audited as the agent's work.
- Browser assistants (claude.ai, Gemini, ChatGPT, Microsoft Copilot) can't connect directly because their connectors need an internet-reachable server, and the app only listens on localhost (PRD section 10). They use copy and paste.
- Fixed: the number check in R9.10 matched numbers inside other numbers ("5" in "25"); it now compares whole numbers.

### Implementation notes (first build)

Where the build differs from, or adds detail to, the requirements in the PRD:

- **File watching (R7.5, D21)** uses polling every few seconds (`RB_SYNC_POLL_SECONDS`, default 3) only. Polling behaves the same on every host OS's Docker mounts; native file events were not added.
- **Tokens (B9a)** are issued on demand with `rb token`, not at bootstrap, because a token created at bootstrap could never be shown to the user.
- **Content rules (R4.1)**: a profile that references a *draft* item syncs with a warning. Final rendering refuses it; a preview allows it (R2.3). Referencing a missing item is always an error.
- **Snapshot protection (R12.10)** also covers the current approved version of each letter and answer, so their version ids never dangle.
- **Stage transitions (PRD section 8.6)**: open stages may move forward and skip stages, but never back.
- **PDF determinism (R12.7)**: the PDF identifier is the snapshot id, and `SOURCE_DATE_EPOCH` is set to the snapshot's creation time while rendering, so embedded font subsets carry a fixed timestamp. Verified byte-for-byte in the Docker image test suite.
- **Milestone 6 (publishing)**: `rb publish` exports the public site to `output/<user>/public/`; pushing it to the target is not built.

### Consistency review of the requirements

Fixes made to the PRD before implementation:

1. **Decisions:** D18–D22 were completed but the note above the table still listed them as open. D15 and D16 were still marked open and now take the recommended choice.
2. **Sample resume vs. confirmed-only rendering:** the seeded public profile selects draft example entries, but only confirmed entries may be rendered (R2.3, R10.13). A watermarked preview mode now covers this (R2.3, R10.12).
3. **Per-user schemas:** JSON Schemas list each user's own company slugs (R11.3), so they can't share one `/data/.schemas/` folder. They now live in `/data/<user>/.schemas/` (R10.14).
4. **Agent identity:** RBAC limits the agent (B7), but nothing said how the API knows who is calling. Local actor tokens are added (B9a).
5. **Backup retention:** frequent pre-change backups would have pushed out all the daily ones. Retention is now counted per kind (DK6).
6. **Lifecycle stages:** "withdrawn" couldn't be reached from `interested`, and "rejected" could be reached before applying. The transitions are now explicit (PRD section 8.6).
7. **Concurrent syncs:** the server's watcher and a CLI container could sync at the same time. A per-user lock is added (R7.5a).
8. **Bootstrap on CLI runs:** every `docker compose run` would have repeated the full bootstrap. One-off CLI containers now run only migrations and the quick sync (R10.1).
9. **Default user:** R1.1 required `--user` on every command; it now defaults to `RB_DEFAULT_USER`.
10. **G5 measure:** onboarding also needs `.env`; the measure now says so.
11. **Where the AI runs:** the PRD described AI-generated drafts but never said where the model runs. The app has no built-in model; an outside agent uses `agent-brief` and the API (see Scope in the PRD).
12. **Citation format:** R9.9 required citations without defining them. The `[cite: …]` marker is now specified.
13. **User docs:** the user manual and quick-start guide became part of the first release.
