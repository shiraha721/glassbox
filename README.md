![](web/public/favicon.svg)

# Glassbox

A local agent workspace for coding, debugging, reverse engineering, CTF challenges and other authorized security work. One tool-using agent works in your project folder, boxed in by an OS sandbox without network access, and in plain view: every model request and tool result is recorded for inspection.

```bash
uv sync --dev --locked && npm --prefix web ci && npm --prefix web run build
uv run glassbox serve    # http://127.0.0.1:8001
```

Then add a provider in **Settings → Providers** (OpenAI, Anthropic, OpenRouter, DeepSeek, ModelScope, OpenCode, your ChatGPT plan, or any compatible endpoint) and pick a model under the message box. `.env` can seed providers instead (see `.env.example`).

Documentation lives in [docs/](docs/README.md):

- [Project guide](docs/README.md): installation, model setup, using Glassbox, isolation and permissions, troubleshooting.
- [Architecture](docs/architecture.md): how a task becomes a recorded Run, the data model, sandboxes, providers and API.
- [Development guide](docs/development.md): conventions, invariants, commands and testing.
- [Harness interfaces](docs/harness.md): CLI, JSON-RPC, Python client, embedding and extensions.
- [Review 2026-10-06](docs/review.md): the latest assessment and changes, with the follow-ups of 2026-10-07 (settings and model selection, then features from comparable agents), 2026-10-08 (usability changes, then an icon and a code review) and 2026-10-09 (one-time code removed, the Glassbox name and icon, multiple providers and ChatGPT plans).

Coding agents start with [AGENTS.md](AGENTS.md).
