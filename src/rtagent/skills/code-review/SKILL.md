---
name: code-review
description: Review a specified change or code path for actionable correctness, security, and regression findings with file locations and evidence.
---

# Code review

1. Establish the requested scope. Use supplied diffs when present. Git metadata is excluded from native workspace tools; do not claim to have read a commit without an available permitted source.
2. Read applicable project instructions and search for callers, data contracts, and checks. Inspect file ranges around the change and follow the relevant control/data path.
3. Look for a concrete trigger and observable failure: invalid state, changed public behavior, missing authorization, unsafe input-to-operation flow, lost data, resource leaks, or recovery errors. Avoid speculative issues and unrelated style requests.
4. If possible, run a harmless targeted reproduction inside the native sandbox. Do not alter implementation or user samples during a review unless asked. Any scratch artifact must stay inside the workspace and be clearly identified.
5. Report findings first, ordered by impact. Include severity, a narrow relative file/line location, triggering input/state, effect, and a focused remedy. Label any unverified hypothesis. If no actionable finding is established, say so and state the scope and verification limits.
6. When explicitly asked for fixes, inspect the current file hashes, apply focused changes, and rerun the original reproduction. Tool permissions still apply.

Use read_text line metadata only as references; it is not file content. A completed plan step records agent progress and does not prove that checks passed. This workflow does not grant write access, load MCP servers, or alter isolation.
