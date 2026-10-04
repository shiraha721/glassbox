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
| [`native_tools`](../../src/rtagent/tools.py#L31) | Build twenty concrete native definitions for the current OS. | Declare bounded strict schemas for file/instruction/patch/plan/memory/Skill tools, native commands, background poll/input/stop, public download and echo. | The actual OS supplies command language; process/download tools retain explicit policy and separate network boundaries. |
| [`native_tools.tool`](../../src/rtagent/tools.py#L34) | Construct one native definition from schema fragments. | Wrap properties/required in an object schema and attach name/description. | Repeated schema boilerplate stays local to the fixed catalog instead of becoming a plugin framework. |
| [`mcp_session`](../../src/rtagent/tools.py#L230) | Yield a fresh initialized SDK session and close all owned transports. | Use AsyncExitStack; stdio receives a minimal baseline environment plus resolved explicit references; HTTP receives optional Bearer credentials in a no-redirect HTTPX client; initialize ClientSession and yield it. | Connection/subprocess lifetime is bounded by discovery/invocation including cancellation. Stateful remote workflows are not assumed and ambient provider keys are not forwarded to stdio. |
| [`ToolRegistry.__init__`](../../src/rtagent/tools.py#L271) | Bind tool catalog, file adapter, and native sandbox. | Resolve workspace, construct Workspace, lazily import WindowsSandbox only on Windows or use BashSandbox elsewhere, and seed reset. | Win32 imports stay out of Linux execution and a missing command sandbox never turns into unrestricted host execution. |
| [`ToolRegistry.for_workspace`](../../src/rtagent/tools.py#L288) | Bind concrete native adapters to a selected root. | Cache registries by canonical Path and synchronize current catalog/errors from the process registry. | Per-project files/sandbox state remain separate; explicitly configured stateless MCP discovery is shared and remains outside command isolation. |
| [`ToolRegistry.reset`](../../src/rtagent/tools.py#L306) | Drop stale discovery and seed native schemas. | Build native catalog keyed by name with input-schema hashes, clear server errors. | Changed MCP settings cannot leave old discovered contracts advertised; enabled unknown names fail until rediscovered. |
| [`ToolRegistry.definition`](../../src/rtagent/tools.py#L342) | Convert one discovered MCP schema to a stable namespaced entry. | Combine server-ID hash, sanitized/truncated original tool name, and original-name hash; retain original name/server/schema/hash. | Distinct servers and sanitized collisions retain deterministic provider-compatible names; dispatch can recover the server's actual name. |
| [`ToolRegistry.discover`](../../src/rtagent/tools.py#L361) | Refresh a bounded catalog from saved servers. | Reset, open each fresh session with a 15-second deadline, read up to 20 pages, normalize entries, and collect type-only safe server failures. | Explicit discovery is manageable without persistent connections or a broker. Results do not grant permission; newly discovered tools default deny through the config map. |
| [`ToolRegistry.selected`](../../src/rtagent/tools.py#L382) | Compute Run-visible tool definitions. | Iterate config permissions, omit deny and native mutation tools under read-only, reject enabled unknown names, and return known allow/ask entries. | Exposure is the intersection of actual discovery and local profile; prompt text cannot create tools. MCP is separate from native read-only mode and still requires its explicit map rule. |
| [`ToolRegistry.read_file`](../../src/rtagent/tools.py#L402) | Read just file content for internal/fixture callers. | Delegate to Workspace.read and return content. | The helper uses identical bounded path validation and does not offer a separate unrestricted read path. |
| [`ToolRegistry.execute`](../../src/rtagent/tools.py#L406) | Dispatch one already authorized frozen definition. | Recheck current native/local mutator and schema constraints, run trusted async handlers, native workspace/sandbox/process/download adapters or stateless MCP; bound output and deadlines. | Runtime owns receipts/core authorization; adapter errors distinguish known results from uncertain custom effects requiring recovery. |
| [`ToolRegistry.bind_repository`](../../src/rtagent/tools.py#L301) | Bind the service's concrete process owner once. | Construct one ProcessManager and share it across project-scoped registry views. | Roots differ while live ownership/persistence remain service-wide; no extra worker or per-project subprocess service exists. |
| [`ToolRegistry.register`](../../src/rtagent/tools.py#L315) | Register an explicitly trusted local adapter. | Validate at most twenty unique nonnative safe names and schemas, attach a handler and writes_workspace flag (true by default), and retain schema hashes/catalog entries across refresh. | Extensions require explicit configuration permission and frozen-schema checks; read-only filters known mutators and registration never executes supplied code. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`ToolRegistry`](../../src/rtagent/tools.py#L268) | Concrete catalog/dispatcher and shared native owners. | Native/MCP/trusted local definitions, errors, root-scoped file/sandbox views and one shared ProcessManager. | Explicit integration suffices without dynamic plugin loading or a generic service hierarchy. |

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
| start_process | Native command/cwd/timeout; original owned process ID/status/PID. | Same OS boundary; up to four live commands, absolute timeout capped by original Run budget, pipes only. |
| poll_process | Original process_id; bounded saved output/lifecycle. | Session/frozen-root ownership, no relaunch or copy control. |
| send_process_input | Original process_id, text/EOF; updated projection. | 8 KiB UTF-8 frame and sixteen slots, closed-input/read-only/current ownership checked; hashes retained. |
| stop_process | Original process_id; final cleanup projection. | Await Job/group exit; completed/partial workspace changes are not undone. |
| fetch_url | HTTPS URL/new path/optional expected SHA-256; digest/size/provenance. | Exact allowed public host, pinned global DNS/TLS, 443, 16 MiB, no redirect/proxy/query/credentials/overwrite; commands retain no network. |
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
def ToolRegistry.bind_repository(self, repository: Repository) -> None
def ToolRegistry.reset(self) -> None
def ToolRegistry.register(self, definition: ToolDefinition, handler: ToolHandler, *, writes_workspace: bool=True) -> None
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
| [`BashSandbox.__init__`](../../src/rtagent/sandbox.py#L26) | Bind the file policy and initialize probe cache. | Store Workspace, backend=bubblewrap, status_cache=None. | Command-launch details remain server infrastructure rather than model-controlled host paths. |
| [`BashSandbox._spawn`](../../src/rtagent/sandbox.py#L32) | Launch an argument-array process with captured pipes. | Use asyncio.create_subprocess_exec, optional piped stdin, stdout/stderr pipes, and start_new_session=true. | Host arguments are never concatenated into shell code; only the deliberate bash -c argument contains model commands. |
| [`BashSandbox._hidden_paths`](../../src/rtagent/sandbox.py#L42) | Find paths that must be masked or reject links. | Walk without following links, use shared exclusions/configured data directory, prune excluded dirs, and fail for symbolic/junction paths including linked excluded roots. | Masking must not expose an indirect credential/runtime destination; unsupported layouts fail rather than falling back to host execution. |
| [`BashSandbox._arguments`](../../src/rtagent/sandbox.py#L66) | Build bubblewrap launch arguments from command/cwd/mode. | Validate cwd; create private namespaces/session/proc/dev/tmp/home, mount read-only /usr with compatibility links, clear env, bind workspace read-only or writable, mask excluded paths, remount root read-only, then invoke Bash without profiles. | OS isolation supplies the actual process boundary. Cwd/path checks complement it; the direct workspace mount preserves local edits without a staging protocol. |
| [`BashSandbox._stop`](../../src/rtagent/sandbox.py#L157) | Kill the whole trusted process group and wait for root exit. | SIGKILL killpg using driver-reported group or process PID, ignore already-gone group, kill remaining root, await process.wait. | A tool must not leave descendants running after normal completion, cancellation, or deadline. |
| [`BashSandbox._drain`](../../src/rtagent/sandbox.py#L168) | Retain and publish bounded Linux pipe output. | Incrementally decode UTF-8 while retaining 24,000 bytes/stream and continue draining past truncation. | Progress precedes completion and capped output cannot deadlock native pipes. |
| [`BashSandbox.execute`](../../src/rtagent/sandbox.py#L208) | Run one native namespace command with optional live pipes. | Stage explicit read-only runtimes under deadline/cancellation, launch trusted setsid marker, stream output, feed bounded input and kill/await the group before final drain/cleanup. | No unrestricted fallback or abandoned preparation thread is allowed; absolute limits and partial-effect evidence remain explicit. |
| [`BashSandbox.status`](../../src/rtagent/sandbox.py#L324) | Return cached or refreshed actual isolation readiness. | Run printf sandbox-ready through read-only execute with 12-second deadline, check exact output/exit, translate AppError, and cache backend/ready/network/detail. | A probe exercises the real OS path and can fail without breaking ordinary HTTP/UI startup. |
| [`BashSandbox._feed_input`](../../src/rtagent/sandbox.py#L190) | Feed owned UTF-8 bytes to namespace stdin. | Consume the bounded controller queue off-thread, write/drain until EOF/stop and close the stream in finally. | Pipe input is explicit and cannot become a host terminal or leave a writer alive after cleanup. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`BashSandbox`](../../src/rtagent/sandbox.py#L23) | Linux-only Bash command adapter. | Concrete bubblewrap/setsid launch, async pipe draining, kill-group cleanup, cached status. | The no-network private mount boundary is OS-enforced; it is not a cwd convention or WSL requirement. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def BashSandbox.__init__(self, workspace: Workspace) -> None
async def BashSandbox._spawn(self, args: list[str], pipe_input: bool=False) -> asyncio.subprocess.Process
def BashSandbox._hidden_paths(self) -> list[Path]
async def BashSandbox._arguments(self, command: str, cwd: str, mode: str, root: str, toolchains: list[Path] | None=None) -> list[str]
async def BashSandbox._stop(self, process: asyncio.subprocess.Process, group: int | None) -> None
async def BashSandbox._drain(stream: asyncio.StreamReader, key: str, publish: Callable[[str, str, bool], None]) -> tuple[str, bool]
async def BashSandbox._feed_input(stream: asyncio.StreamWriter, control: CommandControl) -> None
async def BashSandbox.execute(self, command: str, cwd: str='.', mode: str='workspace-write', timeout: float=60, *, control: CommandControl | None=None, on_output: OutputCallback | None=None, toolchain_roots: list[str] | None=None) -> str
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
| [`_bind`](../../src/rtagent/windows_sandbox.py#L104) | Bind one exported Win32 function with correct ABI types. | Get the DLL symbol and assign restype/argtypes, then return the ctypes callable. | Explicit signatures preserve pointer sizes/alignment and centralize unsafe FFI setup; these callables are OS imports, not extra business functions. |
| [`_check`](../../src/rtagent/windows_sandbox.py#L158) | Convert a false Win32 result into its last-error exception. | Call ctypes.WinError(ctypes.get_last_error()) when success is false. | Native failures cannot be ignored and leave an apparently successful unrestricted/unclean execution path. |
| [`_grant`](../../src/rtagent/windows_sandbox.py#L163) | Grant an AppContainer SID access to disposable paths. | Invoke System32/icacls.exe by argument array with recursive inheritable access, captured output, 15-second timeout, and hidden window; reject nonzero result. | Only the created private workspace/scratch/root ACLs change, never user project or system ACLs. |
| [`_drain`](../../src/rtagent/windows_sandbox.py#L182) | Retain and publish bounded Windows output. | Incrementally decode a reader thread's UTF-8 stream, keep 24,000 bytes, relay retained progress/truncation and read to EOF. | Native waiting stays off-loop without deadlock; live output does not replace the final result. |
| [`WindowsSandbox.__init__`](../../src/rtagent/windows_sandbox.py#L229) | Bind host file policy and status cache. | Store Workspace; set status_cache=None; backend is windows-appcontainer. | Original host workspace identity stays separate from the private command-visible copy. |
| [`WindowsSandbox._run_process`](../../src/rtagent/windows_sandbox.py#L234) | Launch/stop one isolated PowerShell process tree. | Build a small environment and private script/Workspace PSDrive; whitelist stdin/out/err handles; attach zero-capability SID attributes; create suspended hidden process, assign bounded kill-on-close Job, resume, poll stop/deadline, terminate whole Job and wait for child count/readers before collecting exit. | Suspended creation closes the pre-Job child-spawn race; handle whitelisting avoids leaked server resources; tree accounting catches children that close pipes but outlive the root. |
| [`WindowsSandbox._execute`](../../src/rtagent/windows_sandbox.py#L448) | Own AppContainer identity, private runtime/workspace staging and copy-back. | Validate cwd/deadline, create profile/SID/temp, filter copies, grant private read/execute or modify ACLs, run native Job, reconcile only after cleanup and release profile/paths. | Original runtime/project ACLs and credentials remain untouched; Windows requires neither WSL nor a host execution fallback. |
| [`WindowsSandbox.execute`](../../src/rtagent/windows_sandbox.py#L530) | Adapt native execution to async streaming and cancellation. | Create a shielded to_thread worker, relay bounded progress, signal/wait cleanup on cancellation, flush output before final receipt and cap serialized result. | The event loop stays responsive and Stop cannot abandon owned handles/tree/copy-back. |
| [`WindowsSandbox.status`](../../src/rtagent/windows_sandbox.py#L572) | Probe the actual AppContainer path and cache result. | Execute an exact read-only Console.Write marker with 12-second deadline, check output/exit, turn AppError into unavailable detail, and return backend/network fields. | Readiness is evidence of real isolation, not just the existence of powershell.exe; app startup is independent of the probe. |
| [`_feed_input`](../../src/rtagent/windows_sandbox.py#L206) | Own and close the native stdin pipe. | Drain queued bytes in a dedicated thread after successful suspended launch/Job assignment; stop on EOF or input_stop and handle closed pipes. | Only explicit stdin/out/err handles are inherited, and normal EOF does not falsely signal cancellation or partial effects. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`StartupInfo`](../../src/rtagent/windows_sandbox.py#L40) | Win32 STARTUPINFOW layout. | ctypes.Structure includes DWORD/WORD/pointer/handle fields with native alignment. | Correct ABI memory layout is necessary for stdout/stderr/stdin configuration on 64-bit Windows. |
| [`StartupInfoEx`](../../src/rtagent/windows_sandbox.py#L65) | Extended creation information. | Embed StartupInfo plus attribute-list pointer. | AppContainer capability SID and explicit inherited-handle list must be present at process creation, not applied after launch. |
| [`ProcessInfo`](../../src/rtagent/windows_sandbox.py#L71) | Handles/IDs returned by CreateProcessW. | Native process/thread handles and DWORD process/thread IDs. | Suspended launch requires separate ownership of thread resume, root wait/termination, and closing both handles. |
| [`SecurityCapabilities`](../../src/rtagent/windows_sandbox.py#L77) | AppContainer security attributes. | SID pointer, capability pointer, zero count, reserved field. | No network capability is granted; this is the OS enforcement boundary, not a prompt rule. |
| [`JobLimits`](../../src/rtagent/windows_sandbox.py#L83) | JOBOBJECT_EXTENDED_LIMIT_INFORMATION layout. | Process/job times/flags/count, pointer-sized memory/affinity values, and I/O counters. | Set active-process limit 32 and kill-on-close in one native Job Object; memory/disk quotas are not claimed as configured. |
| [`WindowsSandbox`](../../src/rtagent/windows_sandbox.py#L224) | Concrete native Windows execution adapter. | Per-call profile, staged files, ACLs, suspended launch/Job, threaded cleanup, and cached probe. | No Linux dependency is required and failures remain explicit rather than running model code on the host. |

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

Linux provides a direct bind mount and private network namespace; Windows provides a disposable staged copy and AppContainer network restriction. Both reject an unavailable boundary and await local tree cleanup. Foreground output updates executing receipts and numbered events before completion; owned background processes use explicit UTF-8 pipes, absolute deadlines and awaited native cleanup. Full terminal emulation is not claimed.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def _bind(library, name, result, arguments)
def _check(success) -> None
def _grant(path: Path, sid: str, access: str) -> None
def _drain(descriptor: int, result: dict, key: str, publish: Callable[[str, str, bool], None] | None=None) -> None
def _feed_input(stream: BinaryIO, control: CommandControl) -> None
def WindowsSandbox.__init__(self, workspace: Workspace) -> None
def WindowsSandbox._run_process(self, root: Path, sid: VOID, command: str, cwd: str, deadline: float, stop: threading.Event, control: CommandControl | None=None, publish: Callable[[str, str, bool], None] | None=None, toolchains: list[Path] | None=None) -> dict
def WindowsSandbox._execute(self, command: str, cwd: str, mode: str, timeout: float, stop: threading.Event, control: CommandControl | None=None, publish: Callable[[str, str, bool], None] | None=None, toolchain_roots: list[str] | None=None) -> dict
async def WindowsSandbox.execute(self, command: str, cwd: str='.', mode: str='workspace-write', timeout: float=60, *, control: CommandControl | None=None, on_output: OutputCallback | None=None, toolchain_roots: list[str] | None=None) -> str
async def WindowsSandbox.status(self, refresh: bool=False) -> dict
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/command_io.py

**Layer: Infrastructure / native pipe bridge.** Bridges Windows threads or Linux async pipes to bounded explicit input and coalesced output.

**Connections and effects:** Sandboxes own OS handles and total retained output. Controllers carry only IDs/events/queues; async consumers persist progress before final receipts.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`CommandControl.started`](../../src/rtagent/command_io.py#L25) | Publish the actual launched native identity. | Assign PID and signal the owner event loop with call_soon_threadsafe. | Start returns evidence of launch, while setup errors never manufacture a PID. |
| [`CommandControl.feed`](../../src/rtagent/command_io.py#L30) | Queue one explicit input/EOF action. | Check closed/stop state, cap encoded bytes at 8 KiB, preflight sixteen-slot queue capacity and mark stdin closed on EOF. | Backpressure and UTF-8 limits are enforced independently of UI text lengths; partial enqueue is avoided. |
| [`OutputRelay.__init__`](../../src/rtagent/command_io.py#L53) | Bind a single command's consumer/event loop. | Allocate an async frame queue and retain an optional callback. | Native readers do not perform asynchronous database writes from OS threads. |
| [`OutputRelay.publish`](../../src/rtagent/command_io.py#L59) | Forward only retained decoded native output. | Schedule an already bounded stream/text/truncation frame onto the event loop if a consumer exists. | Pipe draining can continue even after the retained output cap without flooding persistence. |
| [`OutputRelay.drain`](../../src/rtagent/command_io.py#L66) | Coalesce and deliver actual progress. | Collect frames for 100 ms, group stdout/stderr and await each persistence consumer until a sentinel. | Real streaming remains responsive without one SQLite transaction per tiny chunk. |
| [`OutputRelay.finish`](../../src/rtagent/command_io.py#L96) | Flush progress before recording command completion. | Append a sentinel and shield-await the consumer task. | Final receipts/cancellation cannot overtake their last retained output frame. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`CommandControl`](../../src/rtagent/command_io.py#L14) | One owned command's live pipe/stop state. | Thread events, bounded byte queue, ready event, PID/closed flag and owning loop; no arbitrary host handles. | Normal input EOF and command stop are separate signals with distinct partial-effect semantics. |
| [`OutputRelay`](../../src/rtagent/command_io.py#L50) | Thread-safe output handoff owner. | One optional async callback, event loop and coalescing queue fed by bounded drains. | OS execution/persistence remain separated without an extra streaming service. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def CommandControl.started(self, pid: int) -> None
def CommandControl.feed(self, text: str='', eof: bool=False) -> None
def OutputRelay.__init__(self, callback: OutputCallback | None) -> None
def OutputRelay.publish(self, stream: str, text: str, truncated: bool=False) -> None
async def OutputRelay.drain(self) -> None
async def OutputRelay.finish(self, reader: asyncio.Task[None]) -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/processes.py

**Layer: Infrastructure / owned native process lifecycle.** Owns at most four live sandbox commands and durable process receipts.

**Connections and effects:** One manager is bound to Repository and shared by project registries. Successful Runs may leave a process alive until explicit stop, absolute deadline or service exit; failed/cancelled Runs stop their original effects. Records never recreate processes after restart.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`process_view`](../../src/rtagent/processes.py#L16) | Project retained lifecycle evidence. | Extract trusted owner/root/mode/command and optional bounded PID/output/input/result/error fields. | Clients see original command evidence without private staging paths or controllable host handles. |
| [`ProcessManager.__init__`](../../src/rtagent/processes.py#L33) | Bind one service's live controls and storage. | Initialize live task/controller/data mapping and a start lock without effects. | A concrete resource owner suffices for a local single-user harness. |
| [`ProcessManager.owned`](../../src/rtagent/processes.py#L39) | Validate control authority. | Read durable ownership, compare session and optional current frozen workspace root; return 404 on mismatch. | Model tools cannot control another conversation's command or follow an idle workspace move into another root. |
| [`ProcessManager.start`](../../src/rtagent/processes.py#L49) | Persist intent before bounded native launch. | Enforce four active tasks, freeze owner/root/mode/timeout/runtime roots, save starting, schedule sandbox drive and await readiness/completion briefly. | Background execution uses the same OS isolation and limits; launch failures remain records, not host fallbacks. |
| [`ProcessManager._drive`](../../src/rtagent/processes.py#L95) | Run and finalize one sandbox command. | Stream retained output into its record, await sandbox exit/tree cleanup/copy-back, save explicit terminal status/result/error and completion time. | No PID/status claims precede native cleanup; private Windows files appear only after exit. |
| [`ProcessManager._drive.output`](../../src/rtagent/processes.py#L100) | Commit real background output. | Append already capped stream text, retain truncation/PID/stdin state and save active progress. | Polling can inspect output while the owning tool call and later conversation turns continue. |
| [`ProcessManager.input`](../../src/rtagent/processes.py#L142) | Feed one still-live original process. | Capture controller/task/data together after owned-record validation, read current settings, recheck completion, then queue bounded input/EOF and persist only hashes/sizes. | Completion across an await returns 409 without a removed-map KeyError, late input or a resurrected terminal receipt; read-only cannot feed older writable commands. |
| [`ProcessManager.stop`](../../src/rtagent/processes.py#L178) | Await stop of a selected owned process. | Resolve authority, signal its controller, shield-await native drive, then read the saved final projection. | A stop response implies Job/group cleanup rather than merely setting a flag. |
| [`ProcessManager.stop_run`](../../src/rtagent/processes.py#L188) | Stop original effects of a cancelled/failed Run. | Iterate live records and match original run_id before invoking stop. | A conversation copy cannot acquire or repeat another Run's background effects. |
| [`ProcessManager.close`](../../src/rtagent/processes.py#L195) | Stop all owned live resources. | Signal every controller and gather drive completion before Repository closes. | Normal service shutdown leaves no owned child process running or deferred copy-back writer. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`ProcessManager`](../../src/rtagent/processes.py#L30) | Concrete live OS-effect owner. | Shared Repository plus bounded controller/task mappings and serialized starts. | Durable process IDs identify records; live controls exist only within the owning service lifetime. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def process_view(record: dict[str, Any]) -> ProcessView
def ProcessManager.__init__(self, repository: Repository) -> None
async def ProcessManager.owned(self, identifier: str, session_id: str, root: Path | None=None) -> dict
async def ProcessManager.start(self, sandbox: Any, args: dict, config: StudioConfig, owner: ToolExecution) -> str
async def ProcessManager._drive(self, identifier: str, run_id: str, sandbox: Any, data: dict, control: CommandControl) -> None
async def ProcessManager._drive.output(frame: dict) -> None
async def ProcessManager.input(self, identifier: str, session_id: str, text: str, eof: bool=False, root: Path | None=None) -> ProcessView
async def ProcessManager.stop(self, identifier: str, session_id: str, root: Path | None=None) -> ProcessView
async def ProcessManager.stop_run(self, run_id: str) -> None
async def ProcessManager.close(self) -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/toolchains.py

**Layer: Infrastructure / explicit runtime staging.** Provides trusted runtime files to native sandboxes without importing the host environment or modifying original ACLs.

**Connections and effects:** Settings stores at most three explicit runtime roots. Windows applies only private-copy AppContainer read/execute ACLs; Linux mounts filtered copies read-only. Commands install/use workspace packages offline.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`validate_toolchains`](../../src/rtagent/toolchains.py#L14) | Validate specific runtime registrations. | Require absolute existing unlinked directories and reject drives, home and workspace ancestors. | Narrow trusted roots avoid silently exposing all host tools, credentials or user directories. |
| [`copy_toolchains`](../../src/rtagent/toolchains.py#L37) | Stage bounded ordinary runtime files. | Filter excluded names, reject links/special files, cap combined entries/bytes, check actual sizes and stop/deadline each entry, preserving relative paths. | Private ACL/mount grants apply to a controlled copy; preparation cancellation waits before deleting its directory. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def validate_toolchains(roots: list[str], workspace: Path) -> list[Path]
def copy_toolchains(roots: list[str], destination: Path, workspace: Path, stop: threading.Event | None=None, deadline: float | None=None) -> list[Path]
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/downloads.py

**Layer: Infrastructure / bounded public dependency transfer.** Implements the separate fetch_url application tool under explicit native tool policy.

**Connections and effects:** This HTTP boundary is outside command OS networking, like application file tools. It accepts only exact allowlisted public HTTPS port-443 hosts, no credentials/query/fragment/redirect/proxy, optional expected hash, and a new workspace destination.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`public_address`](../../src/rtagent/downloads.py#L18) | Resolve and pin a public destination. | Resolve DNS off-thread, require every answer to be a global IP and select one literal address. | Private/local/fake-IP resolution fails before transmission and later DNS changes cannot redirect the request. |
| [`save_download`](../../src/rtagent/downloads.py#L27) | Persist one checked body to a new scoped file. | Validate workspace path, cap 16 MiB, open exclusively and remove a partial file after closing on write failure. | Transfer cannot overwrite project files or leave a misleading successful destination after failed I/O. |
| [`fetch_url`](../../src/rtagent/downloads.py#L49) | Download one bounded allowlisted dependency. | Validate HTTPS/host/URL constraints, pin the public IP with original Host/TLS SNI, disable redirects/proxy/content expansion, bound streamed bytes, verify SHA-256 and save. | Commands retain no network; provenance and digest return as an ordinary tool receipt with no arbitrary internet allowance. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def public_address(host: str) -> str
def save_download(workspace: Workspace, relative: str, body: bytes) -> None
async def fetch_url(workspace: Workspace, hosts: list[str], url: str, path: str, sha256: str | None=None) -> dict
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/packages.py

**Layer: Infrastructure / reusable prompt and Skill data.** Imports/exports small explicit data packages without runtime configuration, credentials or execution state.

**Connections and effects:** Preset persistence remains Repository-owned; Skill resources use validated Workspace paths and lock/staging. No installed plugin code or hidden instruction mutation occurs.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`import_preset`](../../src/rtagent/packages.py#L11) | Construct a new editable preset from a package. | Deep-copy data, assign fresh aggregate/block IDs, set initial revision for ordinary save and preserve explicit templates/roles/orders. | Import never overwrites existing identity or copies effects/checkpoints/configuration. |
| [`export_skill`](../../src/rtagent/packages.py#L25) | Collect one valid procedure and its text resources. | Load validated metadata/body, walk ordinary unlinked files, reject excluded paths and enforce UTF-8 file/count/byte limits. | Reusable instructions include their supporting evidence without exporting private runtime data. |
| [`import_skill`](../../src/rtagent/packages.py#L49) | Atomically publish a new validated procedure directory. | Reject existing/bundled names, preflight portable paths/traversal/case/file-directory aliases, stage all resources, validate frontmatter and rename under the workspace lock. | Invalid packages never leave a partially visible Skill, and publication never executes resource code or changes policy. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def import_preset(package: PresetPackage) -> Preset
def export_skill(workspace: Workspace, name: str) -> SkillPackage
def import_skill(workspace: Workspace, package: SkillPackage) -> dict
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/desktop.py

**Layer: Desktop infrastructure / user interaction.** Owns one native folder dialog independently of model tools and workspace registration.

**Connections and effects:** HTTP calls choose explicitly; the Web lifespan closes it. Launches a short-lived native helper, never a shell command or agent tool. The process-local lock rejects competing dialogs; the helper is terminated and reaped on cancellation, timeout or shutdown.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`FolderPicker.__init__`](../../src/rtagent/desktop.py#L17) | Establish an idle desktop dialog owner. | Create an asyncio lock and empty task/process references with a shutdown flag. | App startup has no GUI side effect; one local request owns one modal capability. |
| [`FolderPicker.close`](../../src/rtagent/desktop.py#L24) | Release an outstanding dialog on shutdown. | Mark closed, cancel the owned task and gather its cleanup. | A native window cannot outlive the Web process's resource lifetime. |
| [`FolderPicker.choose`](../../src/rtagent/desktop.py#L31) | Return a canonical directory or a typed cancellation/fallback outcome. | Reject closed/busy state, normalize an existing initial folder, shield subprocess creation, communicate asynchronously for at most five minutes and resolve the selected Unicode path; kill/reap in finally. | The GUI remains responsive alongside Runs/SSE, cancellation retains prior input, and helper failures enable manual selection rather than breaking registration. |
| [`FolderPicker._command`](../../src/rtagent/desktop.py#L105) | Select concrete OS desktop support. | On Windows launch the real base interpreter with the stdlib Shell helper; on a Linux desktop use installed zenity or kdialog; otherwise return None. | Windows venv redirectors can orphan a child on stub termination. The chooser needs no WSL, package install or speculative desktop abstraction. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`FolderPicker`](../../src/rtagent/desktop.py#L14) | Process-local dialog resource owner. | One lock/task/process plus an explicit closed state. | Selection ownership belongs to the desktop adapter, distinct from saved Projects and command sandbox capabilities. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def FolderPicker.__init__(self) -> None
async def FolderPicker.close(self) -> None
async def FolderPicker.choose(self, initial_path: str) -> FolderSelection
def FolderPicker._command(initial_path: str) -> list[str] | None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/windows_dialog.py

**Layer: Desktop infrastructure / Windows Shell FFI.** Opens Explorer's modern folder picker in an isolated stdlib helper process.

**Connections and effects:** Invoked only by FolderPicker on Windows. Uses ctypes COM/Win32 with explicit pointer/HRESULT signatures and releases all owned Shell items and task-allocated strings. Its single UTF-8 JSON result contains a selected path; it neither edits files nor executes model instructions.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`select_folder`](../../src/rtagent/windows_dialog.py#L10) | Show IFileOpenDialog configured for existing filesystem folders. | Verify an interactive window station, initialize a COM STA, create the dialog, set FOS_PICKFOLDERS/options/title/initial Shell item, show, retrieve SIGDN_FILESYSPATH and release all objects/COM state in finally. | Use the native system UI and Unicode paths without a dependency framework, directory upload or custom tree. |
| [`select_folder.check`](../../src/rtagent/windows_dialog.py#L20) | Recognize a failed COM HRESULT. | Raise OSError when the signed HRESULT is negative; cancellation is handled separately by Show's known code. | Unexpected Shell failures become unavailable at the parent adapter rather than an invented selected path. |
| [`select_folder.method`](../../src/rtagent/windows_dialog.py#L25) | Bind a specific documented COM vtable method. | Cast the interface to its vtable and construct a WINFUNCTYPE callable with HRESULT, this pointer and explicit argument types. | ctypes has no COM wrapper here; exact ABI signatures are required for reliable pointer lifetime and 64-bit calls. |
| [`main`](../../src/rtagent/windows_dialog.py#L94) | Translate native selection into the helper protocol. | Reconfigure stdout to UTF-8, select the initial argument, emit a JSON path or null for cancellation and use exit two for failure. | The asynchronous parent interprets a small outcome protocol without locale-dependent text or traceback parsing. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def select_folder(initial_path: str) -> str | None
def select_folder.check(result: int) -> None
def select_folder.method(instance: ctypes.c_void_p, index: int, *types: type) -> Callable[..., int]
def main() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>
