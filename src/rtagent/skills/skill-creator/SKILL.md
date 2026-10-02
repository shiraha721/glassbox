---
name: skill-creator
description: Create or improve a reusable workspace skill when the user asks to save a repeatable workflow, add a skill, or teach the agent a project procedure.
---

# Create a workspace skill

1. Identify the repeatable task, its trigger, required inputs, and what a successful result looks like. Use the current conversation and workspace; ask only for missing information that changes the workflow.
2. Check existing skills with `list_files` under `.agents/skills`. Use `load_skill` before updating a skill. Keep one focused workflow per skill.
3. Choose a short lowercase hyphenated name. Write a description that says both what the skill does and when to use it.
4. Write concise English instructions: concrete steps, relevant workspace paths, checks for success, and any known failure or recovery steps. Include commands only after inspecting the actual project. Reference supporting files instead of copying large documents into the instructions.
5. Call `save_skill` with `name`, `description`, and the Markdown `instructions` body. When updating, pass the `hash` returned by `load_skill` as `expected_hash`. Do not put YAML frontmatter in the instructions; the tool writes it.
6. Call `load_skill` to verify the saved result. Exercise the workflow on a small representative example when practical; fix errors before reporting success.

Use `write_text` or `edit_text` for genuinely necessary supporting files under the skill directory. Do not add boilerplate files, frameworks, or placeholder steps. Never copy credentials into a skill. A skill cannot change the workspace sandbox or grant itself permission to run tools. If a required tool is denied, explain the missing capability.

Report the saved path and how to invoke the skill: select it in Workspace or mention `$skill-name` in a task. Future runs discover workspace skills automatically.
