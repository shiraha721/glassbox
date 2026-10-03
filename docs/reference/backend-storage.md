# Application persistence, transactions, and conversation copies

Repository is the application's concrete SQLite store. It owns version checks, short serialized write transactions, Run/session claims, persistent events, and effect receipts. LangGraph checkpoint storage is separate and uses its own public SDK lifecycle.

[Reference index](README.md) · [System architecture](../architecture.md)

## src/rtagent/storage.py

**Layer: Persistence / infrastructure.** Implements application-table access using SQLAlchemy Core and async SQLite; data-only aggregates use validated JSON while ownership/indexed identities use columns.

**Connections and effects:** Studio, routes, and Runner call this store. Alembic applies schema migrations off-thread. One process writer lock wraps transactions; reads use independent async connections. SQLite foreign keys, WAL, and busy timeout are configured on each connection.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`_session_view`](../../src/rtagent/storage.py#L122) | Project a stored session row into public metadata. | Convert the mapping to a dictionary and omit title_locked. | Automatic-title policy is an internal storage detail, not a configurable browser field. |
| [`Repository.__init__`](../../src/rtagent/storage.py#L130) | Create a concrete async store and write mutex. | Initialize engine and lock, then register a connect hook on its synchronous engine bridge. | SQLite settings apply per physical connection; the repository keeps short transactions without an ORM or generic persistence framework. |
| [`Repository.__init__.configure_sqlite`](../../src/rtagent/storage.py#L137) | Set integrity/concurrency PRAGMAs for each connection. | Enable foreign_keys, WAL journal mode, and 5000 ms busy timeout using a cursor. | Cascades must actually run; WAL permits concurrent readers and bounded contention handling complements the process writer lock. |
| [`Repository.initialize`](../../src/rtagent/storage.py#L144) | Apply schema and seed usable current settings/preset once. | Run migrate off-thread, then in one writer transaction insert default revision-1 config and Security analysis embedded blocks only if their tables are empty. | Startup upgrades existing data without resetting user edits. Seeded prompts create a usable baseline but are still editable recorded blocks. |
| [`Repository.initialize.migrate`](../../src/rtagent/storage.py#L147) | Upgrade application tables to Alembic head. | Find repository alembic.ini, replace the async driver in the URL for migration work, and call command.upgrade. | Schema evolution is explicit migration history; create_all cannot reliably express upgrades to existing databases. |
| [`Repository.close`](../../src/rtagent/storage.py#L196) | Release the async connection pool. | Await engine.dispose. | Application shutdown owns store cleanup after worker/checkpoint cleanup. |
| [`Repository._one`](../../src/rtagent/storage.py#L200) | Fetch one record by primary identity or raise 404 AppError. | Execute a select using an async connection, take the first mapping, return a dictionary. | Repeated read paths share one consistent missing-record contract without creating an unused generic repository interface. |
| [`Repository.config`](../../src/rtagent/storage.py#L212) | Return the current typed settings aggregate. | Read settings/default JSON through _one and validate StudioConfig. | Persistence data is validated again at the application boundary before use. |
| [`Repository.save_config`](../../src/rtagent/storage.py#L216) | Replace settings at the observed revision. | Under writer lock/transaction compare current revision, copy with revision+1, update JSON, return saved config. | Stale UI saves fail instead of overwriting changes; existing Run JSON is never rewritten. |
| [`Repository.list_presets`](../../src/rtagent/storage.py#L228) | Return current editable preset aggregates. | Select JSON data and validate each as Preset. | Presets own blocks in a single versioned value; no block join/repository is necessary. |
| [`Repository.preset`](../../src/rtagent/storage.py#L234) | Read one typed preset. | Fetch its row and validate the JSON aggregate. | Compilation gets an explicit version and embedded block set rather than shared mutable block references. |
| [`Repository.save_preset`](../../src/rtagent/storage.py#L238) | Create revision-0 preset or update matching revision. | Check existence/revision under writer lock, reject stale or nonexistent nonzero revision, then insert/update an incremented copy. | New drafts and saved edits share one optimistic concurrency boundary. |
| [`Repository.new_session`](../../src/rtagent/storage.py#L259) | Create an empty conversation with independent identity. | Store bounded title, revision 0, empty history/no active Run, no folder/archive/copy origin, and title_locked based on explicit title. | Conversation creation is separate from task execution, and an intentional title survives the first Run's auto-title. |
| [`Repository.session`](../../src/rtagent/storage.py#L277) | Return public history/metadata by ID. | Read the stored row and apply _session_view. | Active Run ownership and successful history are authoritative database state, not browser-only state. |
| [`Repository.list_sessions`](../../src/rtagent/storage.py#L281) | Return all conversations newest-first. | Select rows ordered by creation timestamp descending and strip internal title flag. | Sidebar search/folder/archive filtering can inspect the complete saved local list; no undocumented 100-item truncation applies. |
| [`Repository.list_folders`](../../src/rtagent/storage.py#L293) | Return all folders alphabetically, including empty ones. | Select folder rows ordered by name. | Empty groups remain visible/manageable and grouping does not depend on conversation joins. |
| [`Repository.save_folder`](../../src/rtagent/storage.py#L303) | Create or rename a folder. | Verify existence/revision for renames, check case-insensitive uniqueness via column collation, then insert/update name/revision/timestamp in one transaction. | Concurrent metadata edits cannot create duplicate sidebar names or silently overwrite a rename. |
| [`Repository.delete_folder`](../../src/rtagent/storage.py#L340) | Delete grouping while preserving its histories. | Check folder exists, clear member folder_id and increment their revisions, then delete the folder in one transaction. | Member edits become stale after regrouping, while conversations/Runs/calls keep their ownership. |
| [`Repository.update_session`](../../src/rtagent/storage.py#L354) | Apply a versioned metadata patch. | Check expected revision; optionally lock explicit title, require idle state for archive changes, validate folder membership, distinguish explicit null using model_fields_set, and increment revision only for supplied changes. | Rename/move never mutate messages or snapshots. Archive blocks future execution until restored, and no-op patches do not invent revisions. |
| [`Repository.copy_session`](../../src/rtagent/storage.py#L395) | Produce independent duplicate or successful-turn fork. | Validate source revision/idle/terminal Runs; select fork boundary; reconstruct successful history for forks; create fresh session/Run/call IDs, preserve immutable evidence with provenance, and emit one run.copied event per copied Run in the transaction. | Copying is evidence organization, not execution. No checkpoints/events are replayed, historical usage is inherited evidence, and source deletion cannot delete the copy. |
| [`Repository.put_preview`](../../src/rtagent/storage.py#L508) | Store an immutable compiled preview and private snapshot. | Insert ID/data inside a writer transaction. | Optional inspection can later be consumed by stable ID; compilation output is not a mutable shared browser object. |
| [`Repository.preview`](../../src/rtagent/storage.py#L513) | Read a saved preview payload. | Fetch by ID and return its data field. | A Run references server-owned compiled input instead of trusting a client to resupply schemas/configuration. |
| [`Repository.create_run`](../../src/rtagent/storage.py#L517) | Atomically claim a valid Preview and idle session, or return a retry. | First check key/hash, then expiry/input hash and current session/config/preset revisions; reject archive/busy/consumed preview; insert queued Run, mark active ownership/auto-title, append event sequence 1. | Version validation and ownership claim happen in one transaction, so two submissions cannot consume the same input or edit a running snapshot. |
| [`Repository.submitted_run`](../../src/rtagent/storage.py#L616) | Resolve direct-submission retries before another compile. | Look up Idempotency-Key and compare canonical submission hash; return row or None. | A lost HTTP response can be retried even if its session revision advanced or workspace files changed after acceptance. |
| [`Repository.run`](../../src/rtagent/storage.py#L628) | Read private execution state and frozen input. | Delegate to _one(runs). | Internal use cases/runtime need the original snapshot; Studio.view supplies the narrower browser projection. |
| [`Repository.list_runs`](../../src/rtagent/storage.py#L632) | List a conversation's inspectable turns in creation order. | Select owned Run rows ordered by created_at. | Conversation inspection includes unsuccessful turns even though they are excluded from future model input history. |
| [`Repository._event`](../../src/rtagent/storage.py#L646) | Append an event and update Run projection within an existing transaction. | Read latest sequence, add 1, insert UTC event payload, update latest_event_seq/updated_at plus supplied changes. | Browser replay and saved state cannot disagree due to separate commits; the caller must hold the writer transaction. |
| [`Repository.emit`](../../src/rtagent/storage.py#L663) | Commit an event/state transition. | Take writer lock, begin transaction, delegate to _event. | Runtime/application transitions use one persisted notification mechanism without an in-memory broker. |
| [`Repository.delta`](../../src/rtagent/storage.py#L668) | Persist a streaming text chunk and its event. | Read partial_output, append text, and call _event with model.delta/call ID in the same transaction. | Refresh/reconnection can reconstruct visible progress from the projection instead of losing transient tokens. |
| [`Repository.finish`](../../src/rtagent/storage.py#L682) | Settle one Run, release its session, and commit successful history. | Ignore already terminal Runs; always clear active reference; on success append one user input plus runtime messages marked history and advance session revision; atomically emit final state/reset partial output/approval. | Finish is idempotent for cancellation races. Failed/cancelled evidence remains in Runs without poisoning the next model history or duplicating rendered system blocks. |
| [`Repository.read_events`](../../src/rtagent/storage.py#L734) | Return a bounded replay page after a cursor. | Select seq greater than after, order ascending, limit 200, add schema_version=1 to each mapping. | Persistent ordered pages bound memory and give SSE a stable resume/deduplication contract. |
| [`Repository.call`](../../src/rtagent/storage.py#L747) | Read one effect receipt, returning None when absent. | Use _one(calls) and convert its AppError to None. | Reentrant nodes can distinguish first execution from completed/unknown receipts before doing an effect. |
| [`Repository.save_call`](../../src/rtagent/storage.py#L754) | Upsert intent/outcome evidence for an application call ID. | Under writer transaction test existence then insert/update kind/status/data/result. | The side-effect boundary is durable independently of graph checkpoints; attempt history is preserved by runtime/application callers. |
| [`Repository.trace`](../../src/rtagent/storage.py#L776) | Load a Run's model/tool receipts. | Select rows ordered by receipt ID. | IDs group model positions then tool positions deterministically; this is a stable inspection order, not a timestamp-accurate interleaving claim. |
| [`Repository.recover`](../../src/rtagent/storage.py#L788) | Classify abandoned work and return queued IDs. | In one transaction mark executing receipts outcome_unknown, mark running/cancelling Runs interrupted with process_restart events, and select queued Runs. | Restart never blindly repeats dispatched effects; genuinely queued work can resume automatically while interrupted/approval work waits for explicit action. |
| [`Repository.delete_session`](../../src/rtagent/storage.py#L817) | Delete an idle conversation's application-owned records. | Verify idle/existence, collect Run IDs, delete session (FK cascades Runs/events/calls), remove its previews by snapshot session identity, return IDs. | Source provenance is not ownership; copied records survive. Studio must separately delete checkpoints first, since those are not application foreign-key tables. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Repository`](../../src/rtagent/storage.py#L127) | One concrete application store. | Async SQLAlchemy Core engine plus local writer mutex and explicit table operations. | SQLite is sufficient for one local user; migrations, transaction scopes, and recovery evidence remain inspectable without speculative backends. |

### Tables and data ownership

| Table / source | Stored information | Relationships and reason |
| --- | --- | --- |
| [settings](../../src/rtagent/storage.py#L37) | `id=default`, versioned StudioConfig JSON. | One current settings aggregate; it contains credential references, not resolved values. |
| [presets](../../src/rtagent/storage.py#L43) | Preset ID and JSON including revision/blocks. | Blocks are embedded; editing current data cannot change copied Run JSON. |
| [conversation_folders](../../src/rtagent/storage.py#L49) | ID, case-insensitive unique name, revision, UTC creation time. | Flat grouping, independently retained when empty. |
| [sessions](../../src/rtagent/storage.py#L57) | Title/revision/active Run, committed messages, folder/archive/title-lock/copy origin. | Folder FK uses SET NULL; active Run is an application reference, not a FK cycle; copy origin is descriptive, not a deletion dependency. |
| [previews](../../src/rtagent/storage.py#L71) | ID and compiled/public/private input JSON with expiry. | Run preview_id is unique but deliberately not a FK; copied evidence gets a synthetic ID without creating a runnable preview. Expired previews are rejected; no scheduled expiry purge is implemented. |
| [runs](../../src/rtagent/storage.py#L77) | Owned session ID, unique preview/key, submission hash, snapshot, status/projection, partial text, sequence, approval/error/usage/timestamps/source. | Session FK CASCADE owns execution records. source_run_id is provenance text, so source deletion does not remove copies. |
| [events](../../src/rtagent/storage.py#L100) | `(run_id, seq)` primary key, type, UTC timestamp, payload. | Run FK CASCADE; sequence and projection change in one transaction. SSE reads these records rather than subscribing to an in-memory bus. |
| [calls](../../src/rtagent/storage.py#L109) | ID, Run FK, model/tool kind, status, structured data, optional JSON result. | Run FK CASCADE; receipts record effect certainty independently of graph progress. Original local evidence is retained; presentation redaction is a copy. |

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
async def Repository.new_session(self, title: str='New investigation') -> dict[str, Any]
async def Repository.session(self, identifier: str) -> dict[str, Any]
async def Repository.list_sessions(self) -> list[dict[str, Any]]
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
async def Repository._event(self, connection, run_id: str, event_type: str, payload: dict[str, Any], **changes) -> None
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
