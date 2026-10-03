# Architecture Reference

Reviewed against the repository source on 2026-10-04. Start with [System architecture](../architecture.md) for the whole application and then follow the responsibility you want to inspect. [Project guide](../README.md) covers startup and use; [Development guide](../development.md) defines working constraints.

This reference explains implemented code from the application down to individual functions: **what it does, its inputs/results, how it works, why it exists, its architectural layer, and how it connects to the rest of the system**. Source links point to the reviewed declarations. Private methods and nested helpers are included, rather than only public HTTP handlers.

## Reading map

| Reference | Scope | Questions it answers |
| --- | --- | --- |
| [Contracts and pure logic](backend-core.md) | domain, configuration, compiler, policy, redaction. | What data crosses boundaries? How are messages assembled and permissions decided? Why separate configuration references from secrets? |
| [Composition, use cases, and HTTP](backend-application.md) | main, Studio, all routes and SSE. | Who owns resources? How does a direct task become a frozen Run? Which actions are idempotent/version-bound? |
| [Execution and model adapters](backend-execution.md) | providers, LangGraph nodes/tasks/recovery. | What is actually sent? How do tools return to the model? How do approval, Stop, and uncertain outcomes work? |
| [Persistence and conversation copies](backend-storage.md) | every Repository method, application tables, transactions, duplicate/fork. | Which data is authoritative? Why receipts and checkpoints are separate? Why copies do not replay effects? |
| [Workspace, tools, and isolation](backend-workspace.md) | ordinary files, memory, Skills, native/MCP dispatch, Windows/Linux sandboxes and FFI. | Where can tools read/write? How are processes isolated/cleaned? What happens to Windows staged changes? |
| [Frontend](frontend.md) | components/hooks/transport/state/style and generated contracts. | Who owns saved state versus drafts? How do controls submit actions? Which areas scroll and which remain fixed? |
| [Engineering](engineering.md) | declared dependency packages, config files, migrations, scripts, every named test/helper. | What does each dependency provide? How is the schema upgraded? What does each verification function actually establish? |
| [Anonymous callbacks](callbacks.md) | all first-party Python lambdas and TypeScript callbacks. | What do the smaller predicates, renderers, event handlers, effects, and closures contribute to their owning function? |

## Coverage and boundaries

The reviewed source contains **198 backend functions/methods and 59 backend classes**, **61 frontend named functions/methods and 1 frontend class**, and **91 supporting named functions/methods plus 1 supporting test class**. Each has an individual entry. The callback appendix additionally lists **11 Python lambdas and 292 TypeScript anonymous callbacks**, including verification callbacks. This totals 350 named functions/methods, 303 anonymous functions, and 61 classes.

The actual Python package is `rtagent`; its responsibilities are currently single modules. The npm package is `security-agent-studio-web`. `scripts`, `migrations`, and `tests` are supporting source directories. `skills/skill-creator`, `skills/debugging` and `skills/code-review` are packaged Markdown resources. The proposed future subpackage layout in the development guide is not an implemented directory tree.

Generated OpenAPI declarations have no first-party runtime function bodies. Win32 aliases bind operating-system functions; the Windows reference documents their role separately. Third-party internal functions and every transitive lockfile entry are outside this project's function inventory. The dependency reference covers all direct declarations and transitively supplied packages that this source directly imports.

Inline callback bodies are linked and shown as short mechanism excerpts; their containing named functions explain the complete operation. Source signatures and DTO field declarations are provided for the Python modules. A signature listing is a reading aid, not code intended to run as a new module.

## Keeping the reference accurate

Update the relevant module/function entry and source link when behavior or declaration positions change. Update the whole-system flow when ownership changes. Document a limitation as a limitation rather than describing a future layer or directory as implemented. A documentation-only review should check source symbols, local links/anchors, field/signature examples, and cross-references; it should not initialize user databases, restart the service, or repeat application acceptance suites.
