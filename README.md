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

Coding agents start with [AGENTS.md](AGENTS.md).
