# Contributing

Thanks for helping. Please read this before opening a pull request.

## Sign off your commits (DCO)

Every commit must carry a `Signed-off-by` line, certifying the [Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote the change, or otherwise have the right to submit it under the terms below.

```sh
git commit -s -m "Explain what the change does"
```

Use your real name and an email you can be reached at. Pull requests with unsigned commits can't be merged. To sign off commits you already made: `git rebase --signoff main`.

## License of contributions

The project is licensed under the [GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later). The maintainer may later relicense it under the MIT License or the Apache License 2.0.

So that this stays possible, signing off a commit also means you license your contribution under both the AGPL-3.0-or-later and the [MIT License](https://opensource.org/license/mit). Don't submit code you can't license this way, such as code copied from projects under other licenses.

## Before you start

- Read [CLAUDE.md](CLAUDE.md) for the project rules and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layers and extension points. [docs/PRD.md](docs/PRD.md) is the product spec.
- For a larger change, open an issue first to agree on the approach.
- Prefer an extension point (a plugin operation, a storage backend, a template folder) over a special case in core code.

## Rules that reviews check

- **No real personal data.** Code, templates, tests, docs and commit messages never contain a real person's name, employers, contact details or job data. Tests and examples use only the fictional user in `examples/sample-user/`.
- **Every feature is an operation.** Add it to the registry with a typed input model and a declared permission, so it reaches the API, the CLI and MCP from that one definition. Never check permissions in an interface.
- **Honesty checks live in code.** Never move them into prompts.
- **Services stay independent of storage and files.** Go through `Storage`, `FileStore` and `SecretStore`. Only the sync adapter reads or writes the user's YAML and Markdown.
- **Schema changes are new migrations.** Released templates never change; add a new version. A new resume template (or a visible change in a new version) also needs its preview images: run `scripts/template_previews.py` as its docstring shows, and add the template to the table in the user manual's section 10.
- **Docs change with behavior.** Update `docs/QUICKSTART.md` and `docs/USER-MANUAL.md` in the same pull request as any user-visible change. Write Markdown with one line per paragraph, without hard wraps.
- **The user guides are also the in-app help.** `docs/QUICKSTART.md`, `docs/USER-MANUAL.md` and `docs/AI-AGENTS.md` are rendered at `/help` on the website (the image copies them into the package). Keep links between them relative (`AI-AGENTS.md#some-heading`), and treat headings as anchors other pages link to: `tests/test_help.py` fails if a link or a contextual help link on the website points to a heading that no longer exists. Raw HTML in them is shown as text, not rendered.

## Running the tests

The supported way is inside the Docker image, which includes WeasyPrint for the PDF tests:

```sh
docker build -t resume-builder:ci .
docker run --rm --entrypoint sh resume-builder:ci -c 'cd /app && python -m pytest -q'
```

On Linux or macOS you can also run them in a virtual environment: `pip install -e ".[test]"`, then `python -m pytest -q`. The PDF tests skip without WeasyPrint.

New code needs tests, including for its permission declaration. Keep the RBAC tests and the byte-for-byte PDF test passing.
