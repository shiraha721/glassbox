# Application persistence, transactions, and conversation copies

Repository is the application's concrete SQLite store. It owns version checks, short serialized write transactions, Run/session claims, persistent events, and effect receipts. LangGraph checkpoint storage is separate and uses its own public SDK lifecycle.

[Reference index](README.md) · [System architecture](../architecture.md)

## src/rtagent/storage.py

**Layer: Persistence / infrastructure.** Implements application-table access using SQLAlchemy Core and async SQLite; data-only aggregates use validated JSON while ownership/indexed identities use columns.

**Connections and effects:** Studio, routes, and Runner call this store. Alembic applies schema migrations off-thread. One process writer lock wraps transactions; reads use independent async connections. SQLite foreign keys, WAL, and busy timeout are configured on each connection.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`_session_view`](../../src/rtagent/storage.py#L155) | Project a stored session row into public metadata. | Convert the mapping to a dictionary and omit title_locked. | Automatic-title policy is an internal storage detail, not a configurable browser field. |
| [`Repository.__init__`](../../src/rtagent/storage.py#L163) | Create a concrete async store and write mutex. | Initialize engine and lock, then register a connect hook on its synchronous engine bridge. | SQLite settings apply per physical connection; the repository keeps short transactions without an ORM or generic persistence framework. |
| [`Repository.__init__.configure_sqlite`](../../src/rtagent/storage.py#L171) | Set integrity/concurrency PRAGMAs for each connection. | Enable foreign_keys, WAL journal mode, and 5000 ms busy timeout using a cursor. | Cascades must actually run; WAL permits concurrent readers and bounded contention handling complements the process writer lock. |
| [`Repository.initialize`](../../src/rtagent/storage.py#L178) | Migrate application schema and establish unchanged shipped defaults. | Apply Alembic off-thread, seed missing presets, upgrade only exact legacy/shipped fingerprints and add absent process/input/download=ask, poll/stop=allow and earlier search/plan/patch defaults. | Startup preserves user text/permissions and old snapshots; explicit runtime/download roots are never inferred from host environments. |
| [`Repository.initialize.migrate`](../../src/rtagent/storage.py#L181) | Upgrade application tables to Alembic head. | Find repository alembic.ini, replace the async driver in the URL for migration work, and call command.upgrade. | Schema evolution is explicit migration history; create_all cannot reliably express upgrades to existing databases. |
| [`Repository.close`](../../src/rtagent/storage.py#L240) | Release the async connection pool. | Await engine.dispose. | Application shutdown owns store cleanup after worker/checkpoint cleanup. |
| [`Repository._one`](../../src/rtagent/storage.py#L244) | Fetch one record by primary identity or raise 404 AppError. | Execute a select using an async connection, take the first mapping, return a dictionary. | Repeated read paths share one consistent missing-record contract without creating an unused generic repository interface. |
| [`Repository.config`](../../src/rtagent/storage.py#L256) | Return the current typed settings aggregate. | Read settings/default JSON through _one and validate StudioConfig. | Persistence data is validated again at the application boundary before use. |
| [`Repository.save_config`](../../src/rtagent/storage.py#L260) | Replace settings at the observed revision. | Under writer lock/transaction compare current revision, copy with revision+1, update JSON, return saved config. | Stale UI saves fail instead of overwriting changes; existing Run JSON is never rewritten. |
| [`Repository.list_presets`](../../src/rtagent/storage.py#L272) | Return current editable preset aggregates. | Select JSON data and validate each as Preset. | Presets own blocks in a single versioned value; no block join/repository is necessary. |
| [`Repository.preset`](../../src/rtagent/storage.py#L278) | Read one typed preset. | Fetch its row and validate the JSON aggregate. | Compilation gets an explicit version and embedded block set rather than shared mutable block references. |
| [`Repository.save_preset`](../../src/rtagent/storage.py#L282) | Create revision-0 preset or update matching revision. | Check existence/revision under writer lock, reject stale or nonexistent nonzero revision, then insert/update an incremented copy. | New drafts and saved edits share one optimistic concurrency boundary. |
| [`Repository.list_projects`](../../src/rtagent/storage.py#L303) | Load saved workspace folder choices. | Read project rows ordered by display name. | Bootstrap can render a local project selector without a directory crawler or external catalog. |
| [`Repository.project`](../../src/rtagent/storage.py#L313) | Resolve one saved root by project ID. | Reuse the shared not-found row loader. | Native scope is derived from server-owned metadata rather than tool-controlled paths. |
| [`Repository.create_project`](../../src/rtagent/storage.py#L317) | Register one canonical workspace root. | Under the writer lock reject an existing root and insert stable ID/name/root/timestamp. | Folder identity is simple durable metadata; no generic project configuration framework is needed. |
| [`Repository.new_session`](../../src/rtagent/storage.py#L337) | Create an empty project-bound conversation. | Validate nullable project membership within the writer transaction, insert idle metadata/history, and return its public view. | Stable project IDs bind future work while legacy/null sessions retain the default root. |
| [`Repository.session`](../../src/rtagent/storage.py#L367) | Return public history/metadata by ID. | Read the stored row and apply _session_view. | Active Run ownership and successful history are authoritative database state, not browser-only state. |
| [`Repository.list_sessions`](../../src/rtagent/storage.py#L371) | Return all conversations newest-first. | Select rows ordered by creation timestamp descending and strip internal title flag. | Sidebar search/folder/archive filtering can inspect the complete saved local list; no undocumented 100-item truncation applies. |
| [`Repository.list_folders`](../../src/rtagent/storage.py#L578) | Return all folders alphabetically, including empty ones. | Select folder rows ordered by name. | Empty groups remain visible/manageable and grouping does not depend on conversation joins. |
| [`Repository.save_folder`](../../src/rtagent/storage.py#L588) | Create or rename a folder. | Verify existence/revision for renames, check case-insensitive uniqueness via column collation, then insert/update name/revision/timestamp in one transaction. | Concurrent metadata edits cannot create duplicate sidebar names or silently overwrite a rename. |
| [`Repository.delete_folder`](../../src/rtagent/storage.py#L625) | Delete grouping while preserving its histories. | Check folder exists, clear member folder_id and increment their revisions, then delete the folder in one transaction. | Member edits become stale after regrouping, while conversations/Runs/calls keep their ownership. |
| [`Repository.update_session`](../../src/rtagent/storage.py#L639) | Apply a versioned metadata patch. | Check expected revision; optionally lock explicit title, require idle state for archive changes, validate folder membership, distinguish explicit null using model_fields_set, and increment revision only for supplied changes. | Rename/move never mutate messages or snapshots. Archive blocks future execution until restored, and no-op patches do not invent revisions. |
| [`Repository.copy_session`](../../src/rtagent/storage.py#L705) | Copy evidence through a full conversation or successful prefix. | Assign independent session/Run/receipt IDs, reconstruct only committed successful history, retain frozen request provenance and copy a fresh summary record only when its covered prefix is included. | Workspace files/effects/checkpoints/process rows are never cloned; a copied summary has independent future selection and source provenance. |
| [`Repository.put_preview`](../../src/rtagent/storage.py#L843) | Store an immutable compiled preview and private snapshot. | Insert ID/data inside a writer transaction. | Optional inspection can later be consumed by stable ID; compilation output is not a mutable shared browser object. |
| [`Repository.preview`](../../src/rtagent/storage.py#L848) | Read a saved preview payload. | Fetch by ID and return its data field. | A Run references server-owned compiled input instead of trusting a client to resupply schemas/configuration. |
| [`Repository.create_run`](../../src/rtagent/storage.py#L852) | Atomically claim a valid Preview and idle session, or return a retry. | First check key/hash, then expiry/input hash and current session/config/preset revisions; reject archive/busy/consumed preview; insert queued Run, mark active ownership/auto-title, append event sequence 1. | Version validation and ownership claim happen in one transaction, so two submissions cannot consume the same input or edit a running snapshot. |
| [`Repository.submitted_run`](../../src/rtagent/storage.py#L951) | Resolve direct-submission retries before another compile. | Look up Idempotency-Key and compare canonical submission hash; return row or None. | A lost HTTP response can be retried even if its session revision advanced or workspace files changed after acceptance. |
| [`Repository.run`](../../src/rtagent/storage.py#L963) | Read private execution state and frozen input. | Delegate to _one(runs). | Internal use cases/runtime need the original snapshot; Studio.view supplies the narrower browser projection. |
| [`Repository.list_runs`](../../src/rtagent/storage.py#L967) | List a conversation's inspectable turns in creation order. | Select owned Run rows ordered by created_at. | Conversation inspection includes unsuccessful turns even though they are excluded from future model input history. |
| [`Repository._event`](../../src/rtagent/storage.py#L981) | Append an event and update Run projection within an existing transaction. | Read latest sequence, add 1, insert UTC event payload, update latest_event_seq/updated_at plus supplied changes. | Browser replay and saved state cannot disagree due to separate commits; the caller must hold the writer transaction. |
| [`Repository.emit`](../../src/rtagent/storage.py#L1016) | Commit an event/state transition. | Take writer lock, begin transaction, delegate to _event. | Runtime/application transitions use one persisted notification mechanism without an in-memory broker. |
| [`Repository.delta`](../../src/rtagent/storage.py#L1022) | Persist a streaming text chunk and its event. | Read partial_output, append text, and call _event with model.delta/call ID in the same transaction. | Refresh/reconnection can reconstruct visible progress from the projection instead of losing transient tokens. |
| [`Repository.finish`](../../src/rtagent/storage.py#L1037) | Settle one Run, release its session, and commit successful history. | Ignore already terminal Runs; always clear active reference; on success append one user input plus runtime messages marked history and advance session revision; atomically emit final state/reset partial output/approval. | Finish is idempotent for cancellation races. Failed/cancelled evidence remains in Runs without poisoning the next model history or duplicating rendered system blocks. |
| [`Repository.read_events`](../../src/rtagent/storage.py#L1090) | Return a bounded replay page after a cursor. | Select seq greater than after, order ascending, limit 200, add schema_version=1 to each mapping. | Persistent ordered pages bound memory and give SSE a stable resume/deduplication contract. |
| [`Repository.call`](../../src/rtagent/storage.py#L1103) | Read one effect receipt, returning None when absent. | Use _one(calls) and convert its AppError to None. | Reentrant nodes can distinguish first execution from completed/unknown receipts before doing an effect. |
| [`Repository.save_call`](../../src/rtagent/storage.py#L1110) | Upsert intent/outcome evidence for an application call ID. | Under writer transaction test existence then insert/update kind/status/data/result. | The side-effect boundary is durable independently of graph checkpoints; attempt history is preserved by runtime/application callers. |
| [`Repository.trace`](../../src/rtagent/storage.py#L1132) | Load a Run's model/tool receipts. | Select rows ordered by receipt ID. | IDs group model positions then tool positions deterministically; this is a stable inspection order, not a timestamp-accurate interleaving claim. |
| [`Repository.recover`](../../src/rtagent/storage.py#L1144) | Classify abandoned graph, reduction and process work. | Mark executing receipts/reduction calls outcome_unknown, incomplete summaries/processes interrupted with safe error/closed stdin, clear reduction reservations, emit interrupted Run state and select queued Runs. | Known approvals remain saved; restart never relaunches OS processes or automatically repeats paid/uncertain effects. |
| [`Repository.delete_session`](../../src/rtagent/storage.py#L1212) | Delete an idle conversation's application-owned records. | Verify idle/existence, collect Run IDs, delete session (FK cascades Runs/events/calls), remove its previews by snapshot session identity, return IDs. | Source provenance is not ownership; copied records survive. Studio must separately delete checkpoints first, since those are not application foreign-key tables. |
| [`Repository.effective_history`](../../src/rtagent/storage.py#L383) | Project checked derived context without rewriting originals. | Read the active successful reduction, verify covered-prefix digest, prepend a labeled user-role summary with context:compaction:ID source and append untouched recent messages. | This preserves role/tool pairing and lets each new immutable snapshot record the exact context selection. |
| [`Repository.compaction`](../../src/rtagent/storage.py#L408) | Read one original reduction record. | Select by stable ID through _one. | Source/config/call evidence has independent durable ownership from active-context selection. |
| [`Repository._has_process`](../../src/rtagent/storage.py#L412) | Check whether native effects still own a conversation. | Join active process rows through original Runs and stop at one match. | Archive/delete/workspace moves cannot detach a live writer; copied receipt provenance does not confer process ownership. |
| [`Repository.process`](../../src/rtagent/storage.py#L426) | Read one process intent/progress record. | Use _one on processes without reconstructing handles. | Durable evidence is not an instruction to relaunch an abandoned OS process. |
| [`Repository.save_process`](../../src/rtagent/storage.py#L430) | Commit intent or bounded progress/final state. | Under the writer lock insert once or update only an active record; terminal states ignore late callbacks. | Preparation/input/output races cannot regress a final lifecycle state or lose known completion. |
| [`Repository.list_processes`](../../src/rtagent/storage.py#L453) | List only original conversation process ownership. | Validate session and join processes to its Runs ordered by creation. | Conversation copies receive no live controls and remain independent after source deletion. |
| [`Repository.list_compactions`](../../src/rtagent/storage.py#L465) | List owned summary records chronologically. | Validate session and query its compactions. | Active selection and failed/cancelled/copied evidence remain separately inspectable. |
| [`Repository.begin_compaction`](../../src/rtagent/storage.py#L479) | Reserve one idle restored session. | Check expected revision and active/archive state in a transaction, insert running source-bound job and set compacting_id without incrementing revision. | A new Run cannot race the summarizer; history stays unchanged until verified activation. |
| [`Repository.save_compaction`](../../src/rtagent/storage.py#L512) | Persist calls and atomically activate a checked result. | Ignore terminal rows, verify source revision/hash on success, record failure if stale, clear matching reservation and advance revision only on activation. | Late provider completion cannot overwrite changed context or activate cancelled/crashed text. |
| [`Repository.reset_compaction`](../../src/rtagent/storage.py#L558) | Select original history again. | Check revision and idle state, clear active summary ID and advance revision; retain all source/reduction records. | Restoring context invalidates stale submissions without destroying audit evidence. |
| [`Repository._notify`](../../src/rtagent/storage.py#L999) | Deliver a best-effort committed event to a trusted callback. | Use a typed redacted copy after the transaction, bound delivery to two seconds and persist hook.failed on exception without recursively notifying it. | Observer failures cannot change known effect status or hold the database writer lock; guaranteed delivery/replay is not claimed. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Repository`](../../src/rtagent/storage.py#L160) | One concrete application store. | Async SQLAlchemy Core engine plus local writer mutex and explicit table operations. | SQLite is sufficient for one local user; migrations, transaction scopes, and recovery evidence remain inspectable without speculative backends. |

### Tables and data ownership

| Table / source | Stored information | Relationships and reason |
| --- | --- | --- |
| [settings](../../src/rtagent/storage.py#L42) | `id=default`, versioned StudioConfig JSON. | One current settings aggregate; it contains credential references, not resolved values. |
| [presets](../../src/rtagent/storage.py#L48) | Preset ID and JSON including revision/blocks. | Blocks are embedded; editing current data cannot change copied Run JSON. |
| [conversation_folders](../../src/rtagent/storage.py#L54) | ID, case-insensitive unique name, revision, UTC creation time. | Flat grouping, independently retained when empty. |
| [projects](../../src/rtagent/storage.py#L62) | ID/name/unique canonical workspace_root/UTC creation time. | Registered existing folder; sessions.project_id binds future Runs while historical root snapshots remain immutable. |
| [sessions](../../src/rtagent/storage.py#L70) | Title/revision/active Run, committed messages, project/folder/archive/title-lock/copy origin. | Project FK binds future workspace selection; folder FK uses SET NULL; active Run is an application reference, not a FK cycle; copy origin is descriptive, not a deletion dependency. |
| [previews](../../src/rtagent/storage.py#L87) | ID and compiled/public/private input JSON with expiry. | Run preview_id is unique but deliberately not a FK; copied evidence gets a synthetic ID without creating a runnable preview. Expired previews are rejected; no scheduled expiry purge is implemented. |
| [runs](../../src/rtagent/storage.py#L93) | Owned session ID, unique preview/key, submission hash, snapshot, status/projection, partial text, sequence, approval/error/usage/timestamps/source. | Session FK CASCADE owns execution records. source_run_id is provenance text, so source deletion does not remove copies. |
| [events](../../src/rtagent/storage.py#L114) | `(run_id, seq)` primary key, type, UTC timestamp, payload. | Run FK CASCADE; sequence and projection change in one transaction. SSE reads these records rather than subscribing to an in-memory bus. |
| [compactions](../../src/rtagent/storage.py#L134) | ID/session/status/created/data with frozen source/config/model receipts, usage and summary provenance. | Session FK CASCADE; active context is separately selected and incomplete reductions never replay. |
| [processes](../../src/rtagent/storage.py#L143) | ID/original Run/status/created/data with command/root/mode, bounded output and input hashes. | Run FK CASCADE; copies retain receipts but never clone process rows, handles or effects. |
| [calls](../../src/rtagent/storage.py#L123) | ID, Run FK, model/tool kind, status, structured data, optional JSON result. | Run FK CASCADE; receipts record effect certainty independently of graph progress. Original local evidence is retained; presentation redaction is a copy. |

`checkpoints.db` belongs to AsyncSqliteSaver. Application Alembic does not alter its internal tables. `app.db` and checkpoints are separate SQLite files without a cross-file atomic transaction: receipts bridge the interval between an external effect and a newer graph checkpoint. Unknown results still require explicit review.

### Duplicate and fork algorithm

The source must be idle, at the expected revision, and all copied Runs terminal. Duplicate preserves the complete saved successful message history and all terminal inspection records. Fork chooses an owned succeeded Run, includes inspection records through that chronological boundary, and rebuilds history from only succeeded Runs in that prefix. Failed/cancelled records may be inspectable in the prefix but are never committed as model input.

Each copied session has revision 0, a fresh identity, its own locked copy/fork title, and archived=false. Folder membership is retained. Copied Runs own fresh IDs, synthetic unique preview/key values, and source_run_id; snapshots/wire/results/timestamps/usage remain historical evidence. Call IDs receive the new Run prefix, preserving their positional suffix and original model tool IDs. Call data identifies copied_from_call_id/run_id. Old SSE events are not cloned: one `run.copied` event becomes sequence 1. No graph task or checkpoint is created. Continuing a copy makes a new Run using **current** settings and the **shared** current workspace, not a cloned filesystem.

### Consistency limits

Writer locking protects this process's short transactions; SQLite uniqueness and foreign keys provide additional database constraints. The supported service uses one worker. Model/file/MCP operations never run inside a held database transaction. Metadata revisions and successful-turn revisions invalidate stale submissions; the initial active claim itself does not increment the session revision. File hashes protect a different resource than database revisions. Archive/copy/delete reject an active conversation; rename/move can operate through their own versioned metadata checks.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def _session_view(row: Any) -> dict[str, Any]
def Repository.__init__(self, database_url: str)
def Repository.__init__.configure_sqlite(connection, _record)
async def Repository.initialize(self) -> None
def Repository.initialize.migrate() -> None
async def Repository.close(self) -> None
async def Repository._one(self, table: Table, identifier: str) -> dict[str, Any]
async def Repository.config(self) -> StudioConfig
async def Repository.save_config(self, value: StudioConfig) -> StudioConfig
async def Repository.list_presets(self) -> list[Preset]
async def Repository.preset(self, identifier: str) -> Preset
async def Repository.save_preset(self, value: Preset) -> Preset
async def Repository.list_projects(self) -> list[dict[str, Any]]
async def Repository.project(self, project_id: str) -> dict[str, Any]
async def Repository.create_project(self, name: str, workspace_root: str) -> dict[str, Any]
async def Repository.new_session(self, title: str='New investigation', project_id: str | None=None) -> dict[str, Any]
async def Repository.session(self, identifier: str) -> dict[str, Any]
async def Repository.list_sessions(self) -> list[dict[str, Any]]
async def Repository.effective_history(self, session: dict[str, Any]) -> list[dict[str, Any]]
async def Repository.compaction(self, identifier: str) -> dict[str, Any]
async def Repository._has_process(self, connection: AsyncConnection, session_id: str) -> bool
async def Repository.process(self, identifier: str) -> dict[str, Any]
async def Repository.save_process(self, identifier: str, run_id: str, status: str, data: dict) -> None
async def Repository.list_processes(self, session_id: str) -> list[dict[str, Any]]
async def Repository.list_compactions(self, session_id: str) -> list[dict[str, Any]]
async def Repository.begin_compaction(self, session_id: str, expected_revision: int, data: dict[str, Any]) -> dict[str, Any]
async def Repository.save_compaction(self, identifier: str, data: dict[str, Any], status: str='running') -> None
async def Repository.reset_compaction(self, session_id: str, expected_revision: int) -> None
async def Repository.list_folders(self) -> list[dict[str, Any]]
async def Repository.save_folder(self, name: str, folder_id: str | None=None, expected_revision: int=0) -> dict[str, Any]
async def Repository.delete_folder(self, folder_id: str) -> None
async def Repository.update_session(self, session_id: str, request: SessionUpdate) -> dict[str, Any]
async def Repository.copy_session(self, session_id: str, request: SessionCopy) -> dict[str, Any]
async def Repository.put_preview(self, identifier: str, data: dict[str, Any]) -> None
async def Repository.preview(self, identifier: str) -> dict[str, Any]
async def Repository.create_run(self, preview_id: str, request: dict[str, Any], key: str, submission: dict[str, Any] | None=None) -> dict[str, Any]
async def Repository.submitted_run(self, key: str, submission: dict[str, Any]) -> dict[str, Any] | None
async def Repository.run(self, identifier: str) -> dict[str, Any]
async def Repository.list_runs(self, session_id: str) -> list[dict[str, Any]]
async def Repository._event(self, connection, run_id: str, event_type: str, payload: dict[str, Any], **changes) -> RunEvent
async def Repository._notify(self, value: RunEvent) -> None
async def Repository.emit(self, run_id: str, event_type: str, payload: dict[str, Any], **changes) -> None
async def Repository.delta(self, run_id: str, call_id: str, text: str) -> None
async def Repository.finish(self, run_id: str, status: str, messages: list[dict], reason: str, error: str | None=None) -> None
async def Repository.read_events(self, run_id: str, after: int) -> list[dict[str, Any]]
async def Repository.call(self, identifier: str) -> dict[str, Any] | None
async def Repository.save_call(self, identifier: str, run_id: str, kind: str, status: str, data: dict, result: Any=None) -> None
async def Repository.trace(self, run_id: str) -> list[dict[str, Any]]
async def Repository.recover(self) -> list[str]
async def Repository.delete_session(self, session_id: str) -> list[str]
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

### Stage 3 persisted ownership

`compactions` owns captured source/configuration, rolling model-call receipts, working/final summaries, usage and copy provenance through a session FK. `processes` owns lifecycle intent/output/input hashes through an original Run FK. Session `compaction_id`/`compacting_id` are selection/reservation metadata, deliberately added in place without a cycle or rebuilding history. Startup marks unfinished reduction calls outcome_unknown and process records interrupted, closes stdin and records a partial-effect inspection message. It never recreates OS handles or retries these effects. LangGraph owns its separate checkpoint tables.
