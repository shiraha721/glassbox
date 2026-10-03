# Workspace files, Skills, tool dispatch, and OS sandboxes

This area owns concrete local tools and the distinction between application file checks and operating-system command isolation. Windows uses a disposable AppContainer with PowerShell and a staged workspace; Linux uses bubblewrap with Bash and a direct workspace mount. MCP transports are explicitly outside those command sandboxes.

[Reference index](README.md) · [System architecture](../architecture.md)

## src/rtagent/workspace.py

**Layer: Infrastructure / workspace files and context.** Stores instructions, memory, and procedures as ordinary bounded files and validates every native file-tool path.

**Connections and effects:** ToolRegistry and Studio call Workspace; Studio moves blocking file operations to a thread. context returns canonical Message values to the pure compiler. Files use a local reentrant lock, separate from SQLite transaction locks.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`excluded_name`](../../src/rtagent/workspace.py#L26) | Recognize excluded credentials/runtime/dependency names. | Casefold names; reject .git/.venv/node_modules/var/__pycache__, .env and other .env.* except .env.example, access-token, and database suffixes. | One exclusion vocabulary is shared by file tools and both command adapters, so a command cannot import secrets merely because its transport differs. |
| [`Workspace.__init__`](../../src/rtagent/workspace.py#L39) | Bind file operations to one resolved workspace. | Resolve root/data paths, locate packaged skill resources, and create threading.RLock. | Explicit roots prevent cwd-dependent behavior; nested edit→write uses a reentrant local lock without a database/file framework. |
| [`Workspace.path`](../../src/rtagent/workspace.py#L46) | Resolve one allowed relative destination or reject it. | Reject absolute/traversal/colon paths, inspect each component for exclusions/symlinks/junctions, resolve containment and configured data-directory exclusion, then return Path. | Application file tools enforce allowed destinations before I/O; these checks are not advertised as OS process isolation or protection from a hostile concurrent host filesystem race. |
| [`Workspace.read_path`](../../src/rtagent/workspace.py#L72) | Decode bounded UTF-8 text from an already trusted Path. | Read limit+1 bytes, reject overflow, decode strictly, and translate missing/binary/I/O failures; default limit is 64 KiB and ranged/search/patch callers choose up to 2 MiB. | Different operations retain explicit bounds without granting an unrestricted file-read helper; callers still validate the path before I/O. |
| [`Workspace.read_lines`](../../src/rtagent/workspace.py#L92) | Read a bounded range from a larger source file. | Validate one-based start and 1–500 lines, decode at most 2 MiB, return an escaped-JSON-bounded excerpt with total/next/range/truncation metadata and the complete text hash. | Models can navigate source incrementally and later supply a full-file patch guard without flooding context. |
| [`Workspace._walk`](../../src/rtagent/workspace.py#L122) | Enumerate searchable project files with shared exclusions. | Validate the root, traverse sorted directories without following links, stop after 10,000 scanned entries and report truncation. | Search cannot bypass file-tool credentials/runtime/dependency boundaries, and traversal work is finite. |
| [`Workspace._matches`](../../src/rtagent/workspace.py#L151) | Match a relative file glob. | Apply fnmatchcase and additionally test the pattern without a leading **/ for root-level files. | The small documented glob contract needs no search engine or dependency. |
| [`Workspace.search_files`](../../src/rtagent/workspace.py#L157) | Find relative filenames by glob. | Filter _walk output, retain at most the requested schema-bound limit and 24,000 escaped-JSON bytes, return path/pattern/truncated metadata. | Navigation returns bounded paths rather than launching a host process or dumping the entire tree. |
| [`Workspace.search_text`](../../src/rtagent/workspace.py#L174) | Find literal text with line evidence. | Filter safe filenames, scan at most 1,000 files/8 MiB with a 2 MiB per-file cap, skip binary/oversized/unreadable input, retain bounded matches with line numbers and explicit truncation/skipped counts. | Literal matching avoids unbounded regex execution and supplies evidence the model can follow with ranged reads. |
| [`Workspace.apply_patch`](../../src/rtagent/workspace.py#L235) | Preflight and apply guarded Add/Update/Delete operations. | Parse at most twenty operations, validate all destinations/hashes/exact contexts before effects, compute bounded diff evidence, then atomically replace or guardedly delete each file; later failure raises partial_patch with completed files. | Hashes prevent stale edits/deletes; preflight rejects malformed batches before effects, but cross-file changes are explicitly not one transaction. |
| [`Workspace._replace`](../../src/rtagent/workspace.py#L338) | Share hash-checked per-file atomic replacement. | Lock, validate a destination and prior full text up to 2 MiB, check the expected hash, create parents, write a same-directory temporary file, os.replace and clean leftovers. | Direct-write callers retain 64 KiB limits while patches support larger sources; readers never see a partially written replacement. |
| [`Workspace.read_instructions`](../../src/rtagent/workspace.py#L498) | Load the scoped root-to-directory instruction chain. | Resolve a requested file parent or directory, choose the first nonempty AGENTS.override.md then AGENTS.md at each ancestor, cap combined text at 32 KiB, and return document paths/text/hashes with root_to_leaf precedence. | Models can inspect local constraints before editing without automatic whole-tree instruction injection. |
| [`Workspace.read`](../../src/rtagent/workspace.py#L87) | Return one ordinary bounded document with its hash. | Resolve through path, read at the default 64 KiB context limit, and hash the complete decoded text with digest. | Memory, instructions, Skills and simple edit operations retain the small-file contract; model read_text instead uses read_lines with a full-file hash. |
| [`Workspace.write`](../../src/rtagent/workspace.py#L329) | Create or guardedly replace a directly supplied small file. | Reject content above 64 KiB, then delegate to _replace. | Direct writes, memory and Skills retain their existing bound while focused patches can edit larger source files. |
| [`Workspace.edit`](../../src/rtagent/workspace.py#L366) | Replace one unambiguous literal occurrence. | Lock/read, require nonempty old_text occurring exactly once, then call write with the read hash. | Exact edits make the target explainable and avoid broad accidental replacement or implicit regex/template execution. |
| [`Workspace.list_files`](../../src/rtagent/workspace.py#L378) | List one directory and a truncation flag. | Validate directory, sort names case-insensitively, skip denied children, retain 200 entries with one extra entry to detect truncation. | Recursion is an explicit subsequent tool choice; excluded names do not leak through listing and output is bounded. |
| [`Workspace.memory`](../../src/rtagent/workspace.py#L394) | Read .rtagent/MEMORY.md, or return an empty document/hash. | Resolve its fixed path; use read if present, otherwise hash the empty string. | Notes use the same normal file mechanism and can be created without special database initialization. |
| [`Workspace.skill_name`](../../src/rtagent/workspace.py#L401) | Validate a single skill directory name. | Enforce at most 64 characters and lowercase alphanumeric groups separated by single hyphens. | Skill selection is a catalog identity, never a filesystem path capable of traversal. |
| [`Workspace._skill`](../../src/rtagent/workspace.py#L410) | Parse one bounded SKILL.md into catalog/source data. | Normalize CRLF for YAML framing, safe_load frontmatter, require matching name/nonempty bounded description and body, preserve original text/hash and builtin or relative origin. | Procedures have a small explicit format, safe YAML parsing, and inspectable provenance rather than arbitrary plugin code execution. |
| [`Workspace.skills`](../../src/rtagent/workspace.py#L441) | Discover built-in and workspace procedures plus safe diagnostics. | Enumerate sorted directories, validate names/paths/source, prohibit replacing a bundled name, record per-entry AppError, and cap valid+invalid entries at 100. | A bad local procedure remains visible as an error without hiding the rest of the catalog. Discovery reads bodies to validate but default context only includes metadata. |
| [`Workspace.load_skill`](../../src/rtagent/workspace.py#L469) | Resolve one catalogued procedure with full source. | Validate name, obtain current catalog/errors, return matching skill or skill_unavailable with its diagnostic. | Selection/tool loading is explicit and can report invalid source rather than injecting an arbitrary path. |
| [`Workspace.save_skill`](../../src/rtagent/workspace.py#L478) | Serialize a workspace procedure to SKILL.md. | Reject bundled names, validate description/instructions, safe_dump name/description frontmatter, then guarded write with expected hash. | UI and skill-creator use one authoring path; metadata cannot grant permissions and built-in procedures are read-only. |
| [`Workspace.instructions`](../../src/rtagent/workspace.py#L532) | Capture the first nonempty root instruction document. | Call read_instructions for the root and return its selected AGENTS.override.md or AGENTS.md document, or null. | Composer and initial context use the same literal text/hash/source path; no recursive link loading or second instruction parser exists. |
| [`Workspace.context`](../../src/rtagent/workspace.py#L537) | Capture initial project instructions, notes, catalog and selected bodies. | Build source-labelled system Messages for the chosen root instruction document, nonempty memory, Skill metadata/diagnostics and deduplicated selected Skill source. | File I/O precedes pure compilation and is frozen once per Run; nested instructions are explicit later read_instructions receipts, not repeated history injections. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Workspace`](../../src/rtagent/workspace.py#L36) | Concrete guarded file/context adapter. | Fixed roots/resources, RLock, bounded UTF-8 operations, ordinary Markdown memory/skills. | Useful local behavior needs no vector store, automatic summarizer, generic memory framework, or package registry. |

### Memory and skill source lifecycle

The first nonempty root `AGENTS.override.md` or `AGENTS.md` is captured literally; the harness does not follow its Markdown links during initial context loading. The current repository's root entry points direct a coding agent to docs, which the model can inspect with read_text. Memory is `.rtagent/MEMORY.md`. A project skill lives at `.agents/skills/<name>/SKILL.md`; supporting files are ordinary allowed workspace files. Packaged [debugging](../../src/rtagent/skills/debugging/SKILL.md) and [code-review](../../src/rtagent/skills/code-review/SKILL.md) provide focused inspect/reproduce/verify and read-only review workflows. The packaged [skill-creator](../../src/rtagent/skills/skill-creator/SKILL.md) is resource text, not a Python module or automatic executable plugin. It asks the model to inspect a need, author minimal instructions with save_skill, and reload them for validation. Its actual writes still require the frozen permissions and file hash checks.

Default context contains catalog metadata, not every skill body. Explicit UI selection and catalogued `$skill-name` mentions load initial bodies as system messages with `skill:<name>` source. Later load_skill calls return current full source as recorded tool evidence. Skill/memory text has no authority to approve calls, add MCP servers, or change sandbox mode.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def excluded_name(name: str) -> bool
def Workspace.__init__(self, root: Path, data_dir: Path | None=None) -> None
def Workspace.path(self, relative: str) -> Path
def Workspace.read_path(path: Path, limit: int=FILE_LIMIT) -> str
def Workspace.read(self, path: str) -> dict[str, str]
def Workspace.read_lines(self, path: str, start_line: int=1, line_count: int=200) -> dict[str, Any]
def Workspace._walk(self, path: str) -> tuple[list[Path], bool]
def Workspace._matches(path: str, pattern: str) -> bool
def Workspace.search_files(self, pattern: str='**/*', path: str='.', limit: int=100) -> dict[str, Any]
def Workspace.search_text(self, query: str, path: str='.', pattern: str='**/*', case_sensitive: bool=True, limit: int=100) -> dict[str, Any]
def Workspace.apply_patch(self, patch: str, expected_hashes: dict[str, str] | None=None) -> dict[str, Any]
def Workspace.write(self, path: str, content: str, expected_hash: str | None=None) -> dict[str, str]
def Workspace._replace(self, path: str, content: str, expected_hash: str | None) -> dict[str, str]
def Workspace.edit(self, path: str, old_text: str, new_text: str) -> dict[str, str]
def Workspace.list_files(self, path: str='.') -> dict[str, Any]
def Workspace.memory(self) -> dict[str, str]
def Workspace.skill_name(name: str) -> str
def Workspace._skill(self, path: Path, name: str, builtin: bool) -> dict[str, Any]
def Workspace.skills(self) -> tuple[list[dict[str, Any]], dict[str, str]]
def Workspace.load_skill(self, name: str) -> dict[str, Any]
def Workspace.save_skill(self, name: str, description: str, instructions: str, expected_hash: str | None=None) -> dict[str, str]
def Workspace.read_instructions(self, path: str='.') -> dict[str, Any]
def Workspace.instructions(self) -> dict[str, str] | None
def Workspace.context(self, selected: list[str]) -> list[Message]
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/tools.py

**Layer: Infrastructure / tool registry and execution adapters.** Defines the small native tool catalog, explicit stateless MCP discovery, and one-call dispatch.

**Connections and effects:** Studio selects/snapshots catalog entries; Runner enforces policy before execute. Registry creates an OS-specific sandbox, owns Workspace, and opens each MCP connection in an async context. MCP tools are not executed in AppContainer/bubblewrap.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`native_tools`](../../src/rtagent/tools.py#L24) | Build fifteen native definitions for the current OS. | Declare bounded strict JSON schemas for search, ranged reads, scoped instructions, guarded changes, task plans, native commands, memory, Skills and echo; TaskPlan supplies the plan schema. | Capabilities remain a concrete catalog; command language follows the OS and no provider-specific tool framework is required. |
| [`native_tools.tool`](../../src/rtagent/tools.py#L27) | Construct one native definition from schema fragments. | Wrap properties/required in an object schema and attach name/description. | Repeated schema boilerplate stays local to the fixed catalog instead of becoming a plugin framework. |
| [`mcp_session`](../../src/rtagent/tools.py#L180) | Yield a fresh initialized SDK session and close all owned transports. | Use AsyncExitStack; stdio receives a minimal baseline environment plus resolved explicit references; HTTP receives optional Bearer credentials in a no-redirect HTTPX client; initialize ClientSession and yield it. | Connection/subprocess lifetime is bounded by discovery/invocation including cancellation. Stateful remote workflows are not assumed and ambient provider keys are not forwarded to stdio. |
| [`ToolRegistry.__init__`](../../src/rtagent/tools.py#L221) | Bind tool catalog, file adapter, and native sandbox. | Resolve workspace, construct Workspace, lazily import WindowsSandbox only on Windows or use BashSandbox elsewhere, and seed reset. | Win32 imports stay out of Linux execution and a missing command sandbox never turns into unrestricted host execution. |
| [`ToolRegistry.for_workspace`](../../src/rtagent/tools.py#L236) | Bind concrete native adapters to a selected root. | Cache registries by canonical Path and synchronize current catalog/errors from the process registry. | Per-project files/sandbox state remain separate; explicitly configured stateless MCP discovery is shared and remains outside command isolation. |
| [`ToolRegistry.reset`](../../src/rtagent/tools.py#L247) | Drop stale discovery and seed native schemas. | Build native catalog keyed by name with input-schema hashes, clear server errors. | Changed MCP settings cannot leave old discovered contracts advertised; enabled unknown names fail until rediscovered. |
| [`ToolRegistry.definition`](../../src/rtagent/tools.py#L256) | Convert one discovered MCP schema to a stable namespaced entry. | Combine server-ID hash, sanitized/truncated original tool name, and original-name hash; retain original name/server/schema/hash. | Distinct servers and sanitized collisions retain deterministic provider-compatible names; dispatch can recover the server's actual name. |
| [`ToolRegistry.discover`](../../src/rtagent/tools.py#L275) | Refresh a bounded catalog from saved servers. | Reset, open each fresh session with a 15-second deadline, read up to 20 pages, normalize entries, and collect type-only safe server failures. | Explicit discovery is manageable without persistent connections or a broker. Results do not grant permission; newly discovered tools default deny through the config map. |
| [`ToolRegistry.selected`](../../src/rtagent/tools.py#L296) | Compute Run-visible tool definitions. | Iterate config permissions, omit deny and native mutation tools under read-only, reject enabled unknown names, and return known allow/ask entries. | Exposure is the intersection of actual discovery and local profile; prompt text cannot create tools. MCP is separate from native read-only mode and still requires its explicit map rule. |
| [`ToolRegistry.read_file`](../../src/rtagent/tools.py#L310) | Read just file content for internal/fixture callers. | Delegate to Workspace.read and return content. | The helper uses identical bounded path validation and does not offer a separate unrestricted read path. |
| [`ToolRegistry.execute`](../../src/rtagent/tools.py#L314) | Dispatch one already authorized frozen tool definition. | Recheck native read-only/OS constraints, validate TaskPlan before returning its JSON receipt, execute bounded workspace functions off-thread, cap command deadlines, or recheck a stateless MCP schema before external invocation. | Runtime owns policy and effect receipts; concrete adapters own effects. Plan updates are data receipts, not commands or a second execution loop. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`ToolRegistry`](../../src/rtagent/tools.py#L218) | Concrete catalog/dispatcher. | Native and discovered definitions, per-server errors, file adapter, and OS sandbox. | Only actual local/MCP integrations are needed; there is no speculative provider/plugin registry hierarchy. |

### Native tool surface

| Tool | Arguments and result | Enforcement / architectural role |
| --- | --- | --- |
| list_files | Optional relative path; entries plus truncated flag. | Workspace.path and per-directory 200-entry cap. |
| read_text | Path, start_line, line_count; content, full-file hash, range/total/next/truncation. | Strict UTF-8 source up to 2 MiB, at most 500 lines and 24,000 escaped-content bytes. |
| search_files | Relative root, glob and limit; safe filenames/truncation. | Shared exclusions; at most 10,000 scanned entries and bounded results. |
| search_text | Literal query, root/glob/case/limit; matches with line numbers, skipped count and truncation. | Up to 1,000 files/8 MiB; no regex or host search process. |
| read_instructions | File/directory path; root-to-leaf selected instruction documents/hashes. | Override-before-AGENTS priority at each directory; 32 KiB combined. |
| write_text | Path/content, expected_hash for replacement. | Guarded single-file atomic replace; excluded from read-only exposure and denied again at dispatch. |
| edit_text | Path/old_text/new_text. | Exactly one literal match, then guarded small-file write. |
| apply_patch | Patch text and per-update/delete expected_hashes; file operations, hashes and bounded diffs. | 64 KiB patch, at most twenty files, exact ordered hunks, 2 MiB sources; preflight before per-file atomic effects, explicit partial failure evidence. |
| update_plan | Explanation and one to twelve labelled status items; complete validated plan. | At most one in_progress item; side-effect-free receipt projected into RunView. |
| powershell / bash | Command, optional cwd/timeout; JSON with stdout/stderr/exit/deadline/truncation/isolation/effect fields. | OS-specific isolation; timeout cannot exceed frozen tool_timeout. No host execution fallback. |
| read_memory / save_memory | Read has no args; save requires content/expected_hash. | Fixed MEMORY_PATH with the same bounded file checks. |
| load_skill / save_skill | Read name; save name/description/instructions/optional expected_hash. | Catalog-name validation, protected bundled sources, plain Markdown/YAML format. |
| echo | Text up to 4,000 characters; unchanged text. | Harmless approval-development tool, default ask. |

All ToolRegistry serialized native/MCP results are at most 65,536 UTF-8 bytes. Command streams retain at most 24,000 bytes each before JSON escaping; adapters shrink output again if necessary to fit the whole result. MCP discovery reads at most 20 pages but does not cap entries per server page or claim remote tools are read-only. SDK annotations are not used as authorization. Stdio baseline variables include PATH and platform/temp helpers, plus only explicitly configured environment references; command sandbox environments are more restricted.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def native_tools() -> list[ToolDefinition]
def native_tools.tool(name: str, description: str, properties: dict, required: list[str]) -> ToolDefinition
async def mcp_session(server: MCPServer)
def ToolRegistry.__init__(self, workspace: Path, data_dir: Path | None=None)
def ToolRegistry.for_workspace(self, root: Path) -> 'ToolRegistry'
def ToolRegistry.reset(self) -> None
def ToolRegistry.definition(server: MCPServer, tool) -> ToolDefinition
async def ToolRegistry.discover(self, config: StudioConfig) -> list[ToolDefinition]
def ToolRegistry.selected(self, config: StudioConfig) -> list[ToolDefinition]
def ToolRegistry.read_file(self, relative: str) -> str
async def ToolRegistry.execute(self, definition: ToolDefinition, args: dict, config: StudioConfig) -> str
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/sandbox.py

**Layer: Infrastructure / Linux command isolation.** Runs explicit Bash code inside bubblewrap namespaces with a mounted workspace and no host network.

**Connections and effects:** Selected only outside Windows; current implementation assumes Linux tools /usr/bin/bwrap and setsid. _hidden_paths validates/masks excluded host entries. Async subprocesses and readers are lifecycle-bound to one tool call.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`BashSandbox.__init__`](../../src/rtagent/sandbox.py#L18) | Bind the file policy and initialize probe cache. | Store Workspace, backend=bubblewrap, status_cache=None. | Command-launch details remain server infrastructure rather than model-controlled host paths. |
| [`BashSandbox._spawn`](../../src/rtagent/sandbox.py#L24) | Launch an argument-array process with captured pipes. | Use asyncio.create_subprocess_exec, optional piped stdin, stdout/stderr pipes, and start_new_session=true. | Host arguments are never concatenated into shell code; only the deliberate bash -c argument contains model commands. |
| [`BashSandbox._hidden_paths`](../../src/rtagent/sandbox.py#L34) | Find paths that must be masked or reject links. | Walk without following links, use shared exclusions/configured data directory, prune excluded dirs, and fail for symbolic/junction paths including linked excluded roots. | Masking must not expose an indirect credential/runtime destination; unsupported layouts fail rather than falling back to host execution. |
| [`BashSandbox._arguments`](../../src/rtagent/sandbox.py#L58) | Build bubblewrap launch arguments from command/cwd/mode. | Validate cwd; create private namespaces/session/proc/dev/tmp/home, mount read-only /usr with compatibility links, clear env, bind workspace read-only or writable, mask excluded paths, remount root read-only, then invoke Bash without profiles. | OS isolation supplies the actual process boundary. Cwd/path checks complement it; the direct workspace mount preserves local edits without a staging protocol. |
| [`BashSandbox._stop`](../../src/rtagent/sandbox.py#L113) | Kill the whole trusted process group and wait for root exit. | SIGKILL killpg using driver-reported group or process PID, ignore already-gone group, kill remaining root, await process.wait. | A tool must not leave descendants running after normal completion, cancellation, or deadline. |
| [`BashSandbox._drain`](../../src/rtagent/sandbox.py#L124) | Read a stream to EOF while retaining bounded output. | Read 8192-byte chunks, store only the first 24,000 bytes, mark truncation, decode with replacement. | Continued draining prevents pipe deadlock even when a command produces more output than the model can receive. |
| [`BashSandbox.execute`](../../src/rtagent/sandbox.py#L135) | Run one isolated command and return a bounded JSON report. | Use fixed setsid driver to report trusted group before command code, start concurrent drainers, enforce timeout, shield tree cleanup even on cancellation, collect exit/output/effect fields, and shrink JSON if needed. | Untrusted stdout cannot choose which host group to kill. Cleanup finishes before Stop returns; timeout may leave workspace edits, and command deadline is reported as tool evidence. |
| [`BashSandbox.status`](../../src/rtagent/sandbox.py#L204) | Return cached or refreshed actual isolation readiness. | Run printf sandbox-ready through read-only execute with 12-second deadline, check exact output/exit, translate AppError, and cache backend/ready/network/detail. | A probe exercises the real OS path and can fail without breaking ordinary HTTP/UI startup. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`BashSandbox`](../../src/rtagent/sandbox.py#L15) | Linux-only Bash command adapter. | Concrete bubblewrap/setsid launch, async pipe draining, kill-group cleanup, cached status. | The no-network private mount boundary is OS-enforced; it is not a cwd convention or WSL requirement. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def BashSandbox.__init__(self, workspace: Workspace) -> None
async def BashSandbox._spawn(self, args: list[str], pipe_input: bool=False) -> asyncio.subprocess.Process
def BashSandbox._hidden_paths(self) -> list[Path]
async def BashSandbox._arguments(self, command: str, cwd: str, mode: str, root: str) -> list[str]
async def BashSandbox._stop(self, process: asyncio.subprocess.Process, group: int | None) -> None
async def BashSandbox._drain(stream: asyncio.StreamReader) -> tuple[str, bool]
async def BashSandbox.execute(self, command: str, cwd: str='.', mode: str='workspace-write', timeout: float=60) -> str
async def BashSandbox.status(self, refresh: bool=False) -> dict
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/sandbox_files.py

**Layer: Infrastructure / Windows staging and reconciliation.** Filters and fingerprints a disposable Windows workspace before granting AppContainer access; applies permitted changes only after processes stop.

**Connections and effects:** WindowsSandbox._execute owns this adapter for one command. Shared Workspace.path protects host destinations. Reads bytes rather than bounded UTF-8, because staged project files may be binary; total bytes/entries are independently bounded.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`SandboxFiles.__init__`](../../src/rtagent/sandbox_files.py#L18) | Bind one private copy destination and baseline containers. | Store Workspace/path, empty raw-byte file hash mapping, and original directory set. | Each command tracks its own starting state without changing original ACLs or introducing a persistent workspace clone service. |
| [`SandboxFiles._collect`](../../src/rtagent/sandbox_files.py#L24) | Read a bounded ordinary-file tree with validated host destinations. | Walk without links, exclude credentials/runtime/private root, detect casefold destination aliases, reject special/hard-linked files, cap 10,000 entries and 64 MiB, and return bytes/directories/up to 20 ignored paths. | Copy-in and copy-back use the same admission rules; aliases and unsupported files cannot bypass exclusions or collide on Windows. |
| [`SandboxFiles._collect.fail_walk`](../../src/rtagent/sandbox_files.py#L25) | Abort tree traversal on an unreadable directory. | Re-raise os.walk's OSError callback instead of continuing. | An inaccessible subtree must not look absent and trigger deletion of its host files during reconciliation. |
| [`SandboxFiles.open`](../../src/rtagent/sandbox_files.py#L66) | Create the private filtered snapshot. | Collect host files, create ordered directories, write bytes, and save SHA-256 of each original byte content. | AppContainer gets disposable files with newly assigned permissions, while host ACLs and excluded dependencies/secrets remain untouched. |
| [`SandboxFiles.reconcile`](../../src/rtagent/sandbox_files.py#L76) | Apply staged changes only after checking concurrent host edits. | Collect staged tree, calculate changed/deleted files, lock Workspace, preflight each changed host hash against baseline, then delete/create/replace files and preserve nonempty host directories. | A stale command copy must not overwrite user/other-tool edits. Individual writes are atomic but the multi-file operation can partially fail; ignored paths and changed count are reported. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`SandboxFiles`](../../src/rtagent/sandbox_files.py#L15) | Per-command Windows copy boundary. | Raw-byte hashes/directories, fixed size/entry limits, guarded preflight/copy-back. | Windows AppContainer can work with normal project files without modifying project ACLs or requiring WSL/VM infrastructure. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def SandboxFiles.__init__(self, workspace: Workspace, destination: Path) -> None
def SandboxFiles._collect(self, root: Path) -> tuple[dict[str, bytes], set[str], list[str]]
def SandboxFiles._collect.fail_walk(error: OSError) -> None
def SandboxFiles.open(self) -> None
def SandboxFiles.reconcile(self) -> dict
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/windows_sandbox.py

**Layer: Infrastructure / native Windows command isolation.** The only module using Win32 FFI; launches PowerShell inside a fresh no-capabilities AppContainer and kills its Job Object tree before copying changes back.

**Connections and effects:** Imported lazily on Windows. Blocking Win32/subprocess/file calls run in asyncio.to_thread. ExitStack/finally own handles, SID/profile, pipes, threads, temporary paths, and process termination; normal host execution is never used as a fallback.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`_bind`](../../src/rtagent/windows_sandbox.py#L98) | Bind one exported Win32 function with correct ABI types. | Get the DLL symbol and assign restype/argtypes, then return the ctypes callable. | Explicit signatures preserve pointer sizes/alignment and centralize unsafe FFI setup; these callables are OS imports, not extra business functions. |
| [`_check`](../../src/rtagent/windows_sandbox.py#L152) | Convert a false Win32 result into its last-error exception. | Call ctypes.WinError(ctypes.get_last_error()) when success is false. | Native failures cannot be ignored and leave an apparently successful unrestricted/unclean execution path. |
| [`_grant`](../../src/rtagent/windows_sandbox.py#L157) | Grant an AppContainer SID access to disposable paths. | Invoke System32/icacls.exe by argument array with recursive inheritable access, captured output, 15-second timeout, and hidden window; reject nonzero result. | Only the created private workspace/scratch/root ACLs change, never user project or system ACLs. |
| [`_drain`](../../src/rtagent/windows_sandbox.py#L176) | Drain one Windows pipe into a bounded output result. | A reader thread opens the descriptor, retains the first 24,000 bytes while reading to EOF, and stores UTF-8 replacement-decoded text/truncation. | Synchronous Win32 process waiting cannot deadlock on full stdout/stderr, and output remains bounded. |
| [`WindowsSandbox.__init__`](../../src/rtagent/windows_sandbox.py#L194) | Bind host file policy and status cache. | Store Workspace; set status_cache=None; backend is windows-appcontainer. | Original host workspace identity stays separate from the private command-visible copy. |
| [`WindowsSandbox._run_process`](../../src/rtagent/windows_sandbox.py#L199) | Launch/stop one isolated PowerShell process tree. | Build a small environment and private script/Workspace PSDrive; whitelist stdin/out/err handles; attach zero-capability SID attributes; create suspended hidden process, assign bounded kill-on-close Job, resume, poll stop/deadline, terminate whole Job and wait for child count/readers before collecting exit. | Suspended creation closes the pre-Job child-spawn race; handle whitelisting avoids leaked server resources; tree accounting catches children that close pipes but outlive the root. |
| [`WindowsSandbox._execute`](../../src/rtagent/windows_sandbox.py#L360) | Own AppContainer identity, filtered staging, ACLs, and reconciliation. | Validate cwd, start deadline, create unique profile/SID/temp root, copy files, grant root RX/workspace RX or M/scratch M, run or honor cancellation/deadline, reconcile writable changes, then release SID/profile and validated temp root. | Native isolation works without WSL/admin/user setup. All model edits appear only after descendants stop; a timeout/cancel can still copy partial edits and is labeled accordingly. |
| [`WindowsSandbox.execute`](../../src/rtagent/windows_sandbox.py#L427) | Adapt blocking command work to async tool execution. | Start a to_thread task with threading.Event, shield it, signal/wait for cleanup on cancellation and re-raise, then bound whole JSON output to 64 KiB. | HTTP/event loop stays responsive; cancellation cannot abandon native handles or process cleanup while the runtime settles Stop. |
| [`WindowsSandbox.status`](../../src/rtagent/windows_sandbox.py#L447) | Probe the actual AppContainer path and cache result. | Execute an exact read-only Console.Write marker with 12-second deadline, check output/exit, turn AppError into unavailable detail, and return backend/network fields. | Readiness is evidence of real isolation, not just the existence of powershell.exe; app startup is independent of the probe. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`StartupInfo`](../../src/rtagent/windows_sandbox.py#L34) | Win32 STARTUPINFOW layout. | ctypes.Structure includes DWORD/WORD/pointer/handle fields with native alignment. | Correct ABI memory layout is necessary for stdout/stderr/stdin configuration on 64-bit Windows. |
| [`StartupInfoEx`](../../src/rtagent/windows_sandbox.py#L59) | Extended creation information. | Embed StartupInfo plus attribute-list pointer. | AppContainer capability SID and explicit inherited-handle list must be present at process creation, not applied after launch. |
| [`ProcessInfo`](../../src/rtagent/windows_sandbox.py#L65) | Handles/IDs returned by CreateProcessW. | Native process/thread handles and DWORD process/thread IDs. | Suspended launch requires separate ownership of thread resume, root wait/termination, and closing both handles. |
| [`SecurityCapabilities`](../../src/rtagent/windows_sandbox.py#L71) | AppContainer security attributes. | SID pointer, capability pointer, zero count, reserved field. | No network capability is granted; this is the OS enforcement boundary, not a prompt rule. |
| [`JobLimits`](../../src/rtagent/windows_sandbox.py#L77) | JOBOBJECT_EXTENDED_LIMIT_INFORMATION layout. | Process/job times/flags/count, pointer-sized memory/affinity values, and I/O counters. | Set active-process limit 32 and kill-on-close in one native Job Object; memory/disk quotas are not claimed as configured. |
| [`WindowsSandbox`](../../src/rtagent/windows_sandbox.py#L189) | Concrete native Windows execution adapter. | Per-call profile, staged files, ACLs, suspended launch/Job, threaded cleanup, and cached probe. | No Linux dependency is required and failures remain explicit rather than running model code on the host. |

### Win32 import surface

`_bind` creates the following OS-call aliases at import. They are foreign functions, not additional first-party Python implementations; their role is listed so the isolation mechanism is fully traceable.

| Local alias | OS entry point | Role in the boundary |
| --- | --- | --- |
| _create_profile / _delete_profile | CreateAppContainerProfile / DeleteAppContainerProfile | Create/release a unique package identity per command. |
| _sid_string / _free_sid / _local_free | ConvertSidToStringSidW / FreeSid / LocalFree | ACL SID spelling and explicit ownership of native allocations. |
| _init_attributes / _update_attribute / _delete_attributes | InitializeProcThreadAttributeList / UpdateProcThreadAttribute / DeleteProcThreadAttributeList | Two attributes: AppContainer capabilities and inherited standard handles. |
| _create_process | CreateProcessW | Unicode environment, explicit executable, extended attributes, suspended/no-window launch. |
| _create_job / _set_job / _assign_job | CreateJobObjectW / SetInformationJobObject / AssignProcessToJobObject | Attach a bounded process tree before the initial thread runs. |
| _query_job / _terminate_job | QueryInformationJobObject / TerminateJobObject | Kill descendants and verify active-process count is zero, even if child pipes already closed. |
| _terminate_process / _resume | TerminateProcess / ResumeThread | Clean up failed suspended assignment and start only the assigned process. |
| _wait / _exit_code / _close | WaitForSingleObject / GetExitCodeProcess / CloseHandle | Deadline/stop polling, exit reporting, and handle release. |

### Windows execution steps and practical limits

1. Resolve cwd against allowed host workspace paths. Start the timeout before staging, so setup consumes the command budget.
2. Create a unique AppContainer with no capabilities and a validated private temporary root. Copy at most 64 MiB / 10,000 ordinary entries, excluding configured runtime data, credentials, Git, dependencies, and caches.
3. Grant the package root read/execute, workspace read/execute or modify, and scratch modify. The script is written inside the inherited readable root. Original project ACLs remain unchanged.
4. Build an explicit environment: Windows profile-location helpers for platform operation, private HOME/TEMP/TMP, system-only PATH, and RTAGENT_WORKSPACE/CWD. Provider credentials/proxies/host PATH are not forwarded.
5. Create PowerShell with extended AppContainer/handle-list attributes while suspended; assign a Job with kill-on-close and 32-process limit, then resume. Model text is inside the script body, never used as a host deletion target or launcher executable.
6. Poll root completion, deadline, and cancellation. Always terminate the Job; verify zero active children, close writer handles, join pipe readers, and retrieve exit code.
7. In writable mode preflight and copy changes back. Mark possible partial effects for timeout/cancel. Release SID allocations/profile and delete only the exact verified temporary root.

AppContainer may read Windows resources already available to sandboxed applications; this is not a VM and does not promise every public host file is invisible. Commands cannot use host dependency directories imported into the copy, perform downloads, or keep background jobs. No resource quotas other than command duration/output/process count and staging bounds are configured. File reconciliation assumes the host workspace is not concurrently modified by a hostile process. Its preflight protects ordinary competing edits, but is not a transactional multi-file merge.

Linux provides a direct bind mount and private network namespace; Windows provides a disposable staged copy and AppContainer network restriction. Both reject an unavailable boundary and await local tree cleanup. Command tool output currently appears when the call completes; live command stdout events and interactive/background processes remain future work.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def _bind(library, name, result, arguments)
def _check(success) -> None
def _grant(path: Path, sid: str, access: str) -> None
def _drain(descriptor: int, result: dict, key: str) -> None
def WindowsSandbox.__init__(self, workspace: Workspace) -> None
def WindowsSandbox._run_process(self, root: Path, sid: VOID, command: str, cwd: str, deadline: float, stop: threading.Event) -> dict
def WindowsSandbox._execute(self, command: str, cwd: str, mode: str, timeout: float, stop: threading.Event) -> dict
async def WindowsSandbox.execute(self, command: str, cwd: str='.', mode: str='workspace-write', timeout: float=60) -> str
async def WindowsSandbox.status(self, refresh: bool=False) -> dict
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>
