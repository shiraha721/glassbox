# AGENTS.md

These instructions apply to the entire repository. Read and follow the [Development Guide](docs/development.md), which contains the complete project constraints, behavioral invariants, engineering conventions, and validation commands.

Read in this order:

1. [Project guide](docs/README.md): scope, delivery status, setup, and roadmap.
2. [System architecture](docs/architecture.md): implemented module boundaries, data, APIs, and acceptance criteria.
3. [Development guide](docs/development.md): repository constraints and supported commands.
4. The relevant source modules and configuration. Actual files determine implemented capabilities.

Working rules:

- Keep project documentation and system design in `docs/`. Root `README.md` and `AGENTS.md` are brief entry points. Runtime Skills and test fixtures stay at their functional paths.
- Keep the local single-user workflow simple. Avoid speculative abstractions, services, authentication, or configuration switches.
- Run works directly from task input; Preview is optional. Never silently fall back to simulation.
- Preserve immutable Run snapshots, message roles/sources, tool-call pairing, revision checks, and explicit copy provenance. Conversation copies never replay effects or clone checkpoints.
- Use native Windows AppContainer/PowerShell or Linux bubblewrap/Bash. Never replace OS isolation with unrestricted host execution or require WSL on Windows.
- Resolve credentials on the server only; exclude secrets and local runtime data from snapshots, browser storage, logs, and Git.
- Write identifiers, docstrings, comments, and documentation in English. Public interfaces need types and meaningful docstrings; comments explain constraints and non-obvious decisions.
- Follow the guide's Python/TypeScript tooling and dependency lockfiles. Use Alembic for application schema changes; leave third-party checkpoint tables to their owner.
- Complete one vertical workflow at a time and verify actual service behavior before expanding tests. Do not repeat passing checks without a new reason.
- For documentation-only changes, check content, links, examples, and cross-references. Application builds, service restarts, and test suites are unnecessary.
