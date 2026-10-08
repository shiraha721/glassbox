![](web/public/favicon.svg)

# Security Agent Studio

A local agent workspace for coding, debugging, reverse engineering, CTF challenges and other authorized security work. One tool-using agent works in your project folder, runs commands in an OS sandbox without network access, and records every model request and tool result for inspection.

```bash
uv sync --dev --locked && npm --prefix web ci && npm --prefix web run build
uv run rtagent serve    # http://127.0.0.1:8001
```

Then add a provider and its API key in **Settings → Providers** and pick a model under the message box. `.env` can seed providers instead (see `.env.example`).

Documentation lives in [docs/](docs/README.md):

- [Project guide](docs/README.md): installation, model setup, using the studio, isolation and permissions, troubleshooting.
- [Architecture](docs/architecture.md): how a task becomes a recorded Run, the data model, sandboxes, providers and API.
- [Development guide](docs/development.md): conventions, invariants, commands and testing.
- [Harness interfaces](docs/harness.md): CLI, JSON-RPC, Python client, embedding and extensions.
- [Review 2026-10-06](docs/review.md): the latest assessment and changes, with the follow-ups of 2026-10-07 (settings and model selection, then features from comparable agents) and 2026-10-08 (usability changes, then the studio icon and a code review).

Coding agents start with [AGENTS.md](AGENTS.md).
