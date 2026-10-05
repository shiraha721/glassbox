# Architecture Reference

Reviewed against the repository source on 2026-10-05, including the [completed layering and lifecycle review](../review.md). Start with [System architecture](../architecture.md) for the whole application and then follow the responsibility you want to inspect. [Project guide](../README.md) covers startup and use; [Development guide](../development.md) defines working constraints.

This reference explains implemented code from the application down to individual functions: **what it does, its inputs/results, how it works, why it exists, its architectural layer, and how it connects to the rest of the system**. Source links point to the reviewed declarations. Private methods and nested helpers are included, rather than only public HTTP handlers.

## Reading map

| Reference | Scope | Questions it answers |
| --- | --- | --- |
| [Contracts and pure logic](backend-core.md) | domain, configuration, compiler, policy, redaction. | What data crosses boundaries? How are messages assembled and permissions decided? Why separate configuration references from secrets? |
| [Composition, use cases, and HTTP](backend-application.md) | main/Harness, Studio, AgentClient, CLI/RPC, all routes and SSE. | Who owns resources? How does a direct task become a frozen Run? Which actions are idempotent/version-bound? |
| [Execution and model adapters](backend-execution.md) | providers/Messages, explicit reduction, hooks/ownership, LangGraph nodes/tasks/recovery. | What is actually sent? How do tools return to the model? How do approval, Stop, and uncertain outcomes work? |
| [Persistence and conversation copies](backend-storage.md) | every Repository method, application tables, transactions, duplicate/fork. | Which data is authoritative? Why receipts and checkpoints are separate? Why copies do not replay effects? |
| [Workspace, tools, and isolation](backend-workspace.md) | files/Skills/packages, native/MCP/trusted-local dispatch, live pipes/processes, runtimes/downloads, OS isolation and user folder dialogs. | Where can tools read/write? Who owns native GUI/process cleanup? What happens to Windows staged changes? |
| [Frontend](frontend.md) | components/hooks/transport/state/style and generated contracts. | Who owns saved state versus drafts? How do controls submit actions? Which areas scroll and which remain fixed? |
| [Engineering](engineering.md) | declared dependency packages, config files, migrations, scripts, every named test/helper. | What does each dependency provide? How is the schema upgraded? What does each verification function actually establish? |
| [Anonymous callbacks](callbacks.md) | all first-party Python lambdas and TypeScript callbacks. | What do the smaller predicates, renderers, event handlers, effects, and closures contribute to their owning function? |

## Coverage and boundaries

The reviewed source contains **305 backend functions/methods and 82 classes**; **73 frontend functions/methods and 1 class**; **149 support functions/methods and 1 class**. Every declaration has an individual purpose/mechanism/reason/layer/source entry. The callback appendix covers **29 Python lambdas and 362 TypeScript anonymous callbacks**, including verification callbacks. This totals **611 named declarations**.

The actual Python package is `rtagent`; its domain/application/infrastructure/interfaces packages group concrete modules with checked dependency direction. The npm package is `security-agent-studio-web`. `scripts`, `migrations`, and `tests` are supporting source directories. `skills/skill-creator`, `skills/debugging` and `skills/code-review` are packaged Markdown resources. The development guide and architecture describe the implemented packages, stable entry aliases and dependency direction.

Generated OpenAPI declarations have no first-party runtime function bodies. Win32 aliases bind operating-system functions; the Windows reference documents their role separately. Third-party internal functions and every transitive lockfile entry are outside this project's function inventory. The dependency reference covers all direct declarations and transitively supplied packages that this source directly imports.

Inline callback bodies are linked and shown as short mechanism excerpts; their containing named functions explain the complete operation. Source signatures and DTO field declarations are provided for the Python modules. A signature listing is a reading aid, not code intended to run as a new module.

## Keeping the reference accurate

Update the relevant module/function entry and source link when behavior or declaration positions change. Update the whole-system flow when ownership changes. Document a limitation as a limitation rather than describing a future layer or directory as implemented. A documentation-only review should check source symbols, local links/anchors, field/signature examples, and cross-references; it should not initialize user databases, restart the service, or repeat application acceptance suites.

Run `uv run python scripts/check_docs.py` after installing the development dependencies. This offline audit checks local links, headings, code fences, table rows, every named declaration's exact source line, and anonymous callback coverage. It detects structural omissions and stale anchors; reviewers still verify the explanation against behavior.
