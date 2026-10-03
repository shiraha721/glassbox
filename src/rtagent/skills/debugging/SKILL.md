---
name: debugging
description: Reproduce a software failure, trace its cause, make a focused repair, and verify the original behavior with actual workspace commands.
---

# Debugging

1. Read the user's symptom, relevant AGENTS.md chain, entry point, and existing verification commands. Use search_files/search_text, then read_text ranges. Record what actually failed.
2. Reproduce the failure using the exposed native command tool. On Windows, use PowerShell; on Linux, use Bash. Check executable availability. Report unavailable runtimes; never request unrestricted host execution or pretend a check ran.
3. Trace the failing input through callers and dependencies. State a concrete hypothesis and the observation that would confirm or reject it. Inspect adjacent paths before changing shared behavior.
4. Repair the cause with one exact edit or a guarded apply_patch. Preserve unrelated changes and supplied checks. Do not suppress an exception, weaken validation, or introduce a fallback merely to hide the failure.
5. Run the original reproduction again, followed by the narrow checks needed for adjacent behavior. Read output and exit status. Do not add a large test suite for a reversible small repair.
6. Report the cause, affected files, observed before/after behavior, checks performed, and remaining limits. Save only useful durable project facts in memory, with its current hash.

For a substantial task, maintain a short update_plan. A failed hypothesis should change the next observation or edit; do not repeat the same failing command without new evidence. Stop/restart can leave edits: read current files before continuing.

This workflow does not change permissions or sandbox policy.
