# Frontend components, state ownership, and interaction functions

The browser is a React workspace, not a model client. It reads/submits the local API and subscribes to persisted SSE. React Query owns server projections; component state owns unsaved UI choices/drafts. No frontend code connects to a provider, MCP server, SQLite, or the command sandbox directly.

[Reference index](README.md) · [System architecture](../architecture.md)

## Entry point and view tree

[main.tsx](../../web/src/main.tsx) creates one QueryClient, disables automatic query/mutation retries, enables focus refetch for queries, mounts App inside QueryClientProvider and React.StrictMode, and imports the stylesheet. It has no declared function. The root DOM element comes from [index.html](../../web/index.html). Mutations are ordinary request calls wrapped by page/component busy/error handling, rather than an extra action framework.

```text
App (bootstrap query)
└── Workspace (server projections + page-local controls)
    ├── Projects → DirectoryField (OS folder selection / unavailable manual entry)
    ├── ConversationHistory (project-filtered history, groups, menus, dialogs)
    ├── RunTurn[] (all inspectable turns)
    │   ├── TaskPlan (latest paired recorded plan)
    │   ├── ToolOutput (bounded returned command/file evidence)
    │   └── Recovery (explicit uncertain-call decisions)
    ├── useLivePreview (debounced side-effect-free draft compilation)
    ├── Composer → BlockEditor[] / SkillPrompt[] (preset drafts and file context)
    ├── Inspector → Json (optional Preview and actual Run traces)
    ├── WorkspacePanel → MemoryEditor / SkillEditor (ordinary files)
    ├── ContextPanel / ProcessesPanel (conversation-scoped actions)
    └── Settings (one versioned settings aggregate)
```

The physical layout follows app/features/shared/api. App composes features and owns selection/submission identity; shared Json/PackageActions/useDialogFocus contain reusable presentation with no app/feature dependency. Chat and Inspector use the same Json widget. Pure domain/execution contracts and native adapters remain server-side.

| State | Owner / lifetime | Architectural reason |
| --- | --- | --- |
| bootstrap config/presets/sessions/folders/projects/catalog | React Query cache; reloaded from server. | Server owns revisions, permissions, and persistence. |
| Run list/status/messages/partial text | Query `['runs', session_id]`, SSE invalidation and 2-second fallback polling while nonterminal. | Saved state survives refresh; token events request fresh projections rather than building a second local Run store. |
| Selected session/project | Workspace state plus localStorage `studio.session` and `studio.project` IDs. | Reopening selects history; no credentials, prompts, or full traces are stored there. |
| Selected inspection Run, panel/tab, sidebar/modal/filter/group choices | Component state for current page. | Navigation is local presentation state; choosing an old turn does not change the current execution target. |
| Preset drafts/undo/redo | usePresetDrafts keyed by preset ID; page-only. | Switching preserves edits, saving advances server revision, reload discards unsaved state. |
| Task/variables/selected skills/live inspection | Workspace plus useLivePreview; debounce 300 ms and retain the last valid result during compilation/errors. | Inspection never autosaves; Run always submits current input through the snapshot compiler. |
| Submission retry identity | Workspace ref with visible fingerprint, complete original body and random key until acceptance or definitive rejection. | An unchanged uncertain retry finds the original Run even after history revisions advance. |
| Memory/skill/settings draft | Dedicated editor state; server hash/revision on save. | Concurrent edits are rejected by the authoritative resource boundary. |

## API client and event subscriber

## web/src/api/client.ts

**Layer: Frontend transport adapter.** Offers typed domain aliases from generated schemas and one JSON fetch path.

**Connections and effects:** All components use request; browser paths remain same-origin /api/v1. This adapter exports only schema aliases consumed by the frontend; generated.ts retains the complete API contract. Error construction preserves server status/code for conflict and folder-selection handling.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`ApiError.constructor`](../../web/src/api/client.ts#L26) | Construct a displayable API failure with status/code. | Extend Error, set its message, and retain readonly numeric status/string code constructor properties. | UI can show a useful message while preserving machine-readable conflict/validation identity. |
| [`request`](../../web/src/api/client.ts#L36) | Fetch a typed local API resource. | Apply JSON/idempotency headers, optional AbortSignal, parse public errors, and handle empty responses. | Live inspection can cancel obsolete requests; server validation remains authoritative. |
| [`errorMessage`](../../web/src/api/client.ts#L68) | Turn an unknown caught value into display text. | Return Error.message or a stable fallback. | Every local editor/action can handle JavaScript and HTTP failures without unsafe type assumptions. |
| [`workspacePath`](../../web/src/api/client.ts#L73) | Address the selected project file API. | Add encoded project_id using the correct query separator while preserving explicit refresh options. | Every memory/skill/probe request uses the same root selection rather than hand-built inconsistent URLs. |
| [`terminal`](../../web/src/api/client.ts#L78) | Check whether a Run is succeeded/failed/cancelled. | Test membership in the three terminal state names. | Controls/polling/stream ownership agree; waiting_approval/interrupted remain nonterminal and cannot be copied/deleted as active conversations. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`ApiError`](../../web/src/api/client.ts#L25) | Typed transport failure. | Error message plus HTTP status/application code. | Error handling stays in the presentation boundary rather than inventing separate frontend business exceptions. |

## web/src/api/events.ts

**Layer: Frontend transport / server-state synchronization.** Subscribes to one active Run and coalesces event-triggered query refresh.

**Connections and effects:** Called only by Workspace for an active Run. Browser EventSource manages reconnection/Last-Event-ID. Cleanup closes the subscription/timer; it does not call cancel/resume/start.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`useRunEvents`](../../web/src/api/events.ts#L25) | Own an EventSource while a Run subscription is enabled. | Register known events with a sequence cursor and bounded refresh timer; cleanup closes the stream and flushes projection/file-context invalidations even when SSE never connected. | Unsubscription after terminal state cannot discard the final authoritative session/context projection. |
| [`useRunEvents.refresh`](../../web/src/api/events.ts#L32) | Reload saved projections after observed events. | Clear the pending timer and invalidate Runs, bootstrap, Context, processes and trace query families. | Active reservations/revisions, evidence and reduction controls become consistent together. |
| [`useRunEvents.receive`](../../web/src/api/events.ts#L40) | Deduplicate a saved event and refresh at the appropriate time. | Parse RunEvent, ignore old sequence values, batch ordinary updates for 80 ms and immediately flush/close on a terminal event. | Replay never repeats effects, and completion cannot remain hidden behind a cancelled timer. |

The subscriber initially replays from sequence 0. It handles only execution event types, because copied Runs are already terminal and read through normal queries; `run.copied` need not drive an active subscription. Its effect cleanup lambda closes EventSource and clears the pending timer. The effect-local cursor and browser Last-Event-ID apply within that subscription; a reload fetches persisted Runs and may safely replay again. Interrupted/waiting Runs keep fallback polling because they are not terminal.

## web/src/app/App.tsx

**Layer: Frontend application / workspace presentation.** Coordinates loaded server state, task submission, local controls, the full transcript, and optional utility panes.

**Connections and effects:** Uses usePresetDrafts, useRunEvents, React Query, and request. It sends revision/hash-bound actions rather than executing business logic locally. Child callbacks funnel effects through perform and refresh; pure rendering consumes RunView.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`App`](../../web/src/app/App.tsx#L48) | Load the local workspace directly. | Query bootstrap, render loading/retry state on failure, then render Workspace with saved data. | There is no login/access-token screen or implicit model call on page open. |
| [`Workspace`](../../web/src/app/App.tsx#L69) | Coordinate explicit navigation, chat and auxiliary views. | Validate persisted IDs, store project/session together, derive Runs from authoritative queries, keep draft/inspection state separate and key conversation-specific editors. | An empty session stays a new draft; inspecting old evidence does not choose another execution target or lose local form state. |
| [`Workspace.refresh`](../../web/src/app/App.tsx#L147) | Reload saved projections after an accepted action. | Invalidate bootstrap, Run lists, traces, and project-keyed workspace context. | File changes and metadata changes become visible alongside execution evidence. |
| [`Workspace.perform`](../../web/src/app/App.tsx#L156) | Apply uniform busy/error handling to one user operation. | Set busy/clear error, await callback, turn failures into display text, always clear busy. | Repeated clicks are disabled while dependent actions run; errors remain visible instead of disappearing into promises. |
| [`Workspace.chooseSession`](../../web/src/app/App.tsx#L167) | Select a conversation and its project as one target. | Update the combined selection, persist IDs, honor explicitly returned membership, clear task/variables/Skills/pending retry/inspection and reset follow-latest. | Selecting a project leaves an explicit new draft rather than implicitly choosing its first history entry. |
| [`Workspace.newSession`](../../web/src/app/App.tsx#L184) | Open history in the selected project. | POST nullable project_id, refresh, select returned IDs, and optionally keep task text. | Direct Run can create its conversation without a Preview gate or an unintended default workspace. |
| [`Workspace.savePreset`](../../web/src/app/App.tsx#L191) | Persist an intentional dirty preset. | PUT the observed aggregate, commit its returned baseline, and refresh; unchanged drafts return immediately. | Run binds saved revisions, while live inspection can compile unsaved edits without writing them. |
| [`Workspace.inputRequest`](../../web/src/app/App.tsx#L198) | Build a direct revision-bound task request. | Parse variables as a non-array object, save dirty preset, use current or newly created session, attach current session/preset/config revisions, input, variables, and skill names. | Submission ties visible task context to actual server state; malformed variables fail before a model request. |
| [`Workspace.buildPreview`](../../web/src/app/App.tsx#L215) | Enable live draft inspection, including empty task input. | Set previewEnabled and open the Inspector Preview tab; useLivePreview supplies subsequent compiled results. | Preview has no save/create/model effect and remains optional. |
| [`Workspace.start`](../../web/src/app/App.tsx#L220) | Submit current input or retry the same uncertain HTTP acceptance. | Retain a fingerprint/body/key, reuse them after history advances, clear on definitive rejection, cache the returned Run immediately, then refresh and clear accepted draft state. | Transport uncertainty cannot change submission identity; the toolbar displays the newly accepted Run while revalidation is pending. |
| [`Workspace.changeInput`](../../web/src/app/App.tsx#L265) | Edit task text. | Update local input only; the serialized draft drives a debounced inspection. | The last valid preview remains visible while a replacement is compiling. |
| [`Workspace.start.fingerprint`](../../web/src/app/App.tsx#L222) | Identify an unchanged visible submission for a retry. | Serialize selected session/root, task, variables, Skills and preset content while excluding revisions and transient server state. | History can advance after acceptance while a lost HTTP response is retried; the original request body/key must remain stable. |

### App callbacks and rendering boundaries

ConversationTurn's Markdown image renderer returns an inert label for any image source, avoiding automatic requests to model-supplied external URLs. react-markdown uses skipHtml, so generated HTML is not executed. The follow-latest effect scrolls only while the user's view is near the bottom; scrolling more than 100 pixels away stops automatic following. Selecting a Run scrolls its DOM turn into view and changes Inspector state only.

Inline action callbacks submit: fork at a specific succeeded Run, approve/reject a specific displayed approval ID, resume explicit receipt resolutions, restore a revisioned archived conversation, or cancel the active Run. They await request then refresh through perform. Composer edits, variables, skill selection, saved Settings, and refreshed project file context change the live draft key; the last valid preview remains visible while updates compile. The Ctrl/Cmd+Enter handler uses the same start function as Run and rejects empty/busy/active input. Utility Prompt/Inspector panes stay mounted but hidden when toggled so local editor state survives; WorkspacePanel is mounted on demand.

## web/src/features/prompts/useLivePreview.ts

**Layer: Frontend state / draft inspection.** Debounces server compilation of an optional current editor draft.

**Connections and effects:** Receives serialized input and file-context version; only calls /prompts/inspect with AbortSignal and never persists execution records.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`useLivePreview`](../../web/src/features/prompts/useLivePreview.ts#L5) | Keep live draft results stable and current. | Wait 300 ms, parse variables, compile through HTTP, abort/ignore superseded closures, and key errors/results to the current body/context version. | Typing does not blank valid evidence or autosave; stale responses cannot replace a newer draft and invalid input has explicit feedback. |

## web/src/features/projects/Projects.tsx

**Layer: Frontend presentation / project navigation.** Lists saved workspace folders and registers a new one through an explicit form.

**Connections and effects:** Workspace owns selection and effects; metadata arrives through bootstrap; no browser file-system or provider access is introduced.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`Projects`](../../web/src/features/projects/Projects.tsx#L9) | Render saved roots and a native folder registration form. | Show default/registered choices, use DirectoryField before manual fallback, infer a draft name from a selected folder and keep modal keyboard focus scoped. | Ordinary registration is an OS folder action without extra configuration or a custom filesystem browser. |
| [`Projects.submit`](../../web/src/features/projects/Projects.tsx#L36) | Register the selected folder and choose its returned project. | Require completed picker/name/path, POST name/root, await metadata refresh and close only on success. | Busy native selection cannot submit or dismiss stale form state; errors preserve the user's draft. |

## web/src/features/prompts/usePresetDrafts.ts

**Layer: Frontend state / local editor history.** Maintains independent page-local preset drafts and bounded undo/redo.

**Connections and effects:** Workspace owns the hook; Composer calls its commands. Saved presets arrive from bootstrap, but only explicit save writes to the backend. Draft comparisons use serialized aggregate values; no browser database or generalized undo framework is introduced.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`usePresetDrafts`](../../web/src/features/prompts/usePresetDrafts.ts#L14) | Return the selected draft, merged preset options, and editor commands. | Key drafts by ID; derive selected present/saved baseline, dirty status, merged options, and canUndo/canRedo. | Switching never discards another preset's draft, and saved server data remains separate from unsaved local edits. |
| [`usePresetDrafts.change`](../../web/src/features/prompts/usePresetDrafts.ts#L29) | Apply one draft edit with optional typing coalescing. | Functional state update; find latest baseline/present, skip unchanged existing draft, merge edits in same field within 700 ms, otherwise push up to 60 past snapshots; clear future and retain saved revision. | Undo granularity tracks user actions without one snapshot per keystroke, and API writes always carry the current saved version. |
| [`usePresetDrafts.travel`](../../web/src/features/prompts/usePresetDrafts.ts#L58) | Undo or redo the selected draft. | Pop the directional stack, push present to the opposite stack, replace present with the latest saved revision, and clear edit group. | Reversible content history does not roll back database revision counters, which would create false conflicts. |
| [`usePresetDrafts.commit`](../../web/src/features/prompts/usePresetDrafts.ts#L79) | Accept a successful saved preset as baseline. | Functional update sets saved/present to response, preserves existing past, clears future/edit group. | Save can remain undoable as an editor action while the next save uses the new server revision. |
| [`usePresetDrafts.discard`](../../web/src/features/prompts/usePresetDrafts.ts#L93) | Revert the selected preset's changes only. | For a new revision-0 draft remove its entry and choose first saved preset; otherwise change to saved baseline. | Other drafts remain intact and the selector stays usable; discarding an existing edit can itself be undone through the normal history. |

The returned `select(next)` callback only changes selected ID; `undo()` and `redo()` call travel with fixed directions. Functional setDrafts callbacks derive their result from the latest state, avoiding closure-based overwrites. The `Draft` type contains present/saved/past/future plus optional lastGroup/lastEdit; it belongs to frontend editor state, not the API schema. All drafts are lost on reload. The hook currently expects at least one saved preset, satisfied by Repository initialization; deleting every preset is not an implemented feature.

## web/src/features/prompts/Composer.tsx

**Layer: Frontend presentation / prompt editing.** Edits embedded Block values in an explicitly selected Preset with separate create/duplicate/rename and reversible changes.

**Connections and effects:** Calls parent onChange/onSelect/onSave/undo/redo/discard; it never compiles or contacts the model. BlockEditor holds only unapplied advanced JSON text/error state; domain validation still happens on the server.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`ordered`](../../web/src/features/prompts/Composer.tsx#L53) | Normalize visible block ordering into compiler placements. | Copy/sort by before_history versus before_input while preserving within-slot array order, then assign unique orders spaced by 10. | Reordering a block must not change role or cross a conversation boundary implicitly; server compiler orders remain unambiguous. |
| [`Composer`](../../web/src/features/prompts/Composer.tsx#L60) | Arrange preset blocks alongside actual workspace sources. | Render reversible preset controls, before-history blocks, AGENTS/memory/catalog/selected skill source controls, history boundary, before-input blocks, current-input boundary, and save controls. | Injected sources are visible at their actual compiler position; ordinary files are not duplicated into editable preset blocks. |
| [`Composer.update`](../../web/src/features/prompts/Composer.tsx#L85) | Replace blocks in the current preset. | Call onChange with ordered(blocks) and preserved preset identity/revision. | Structural actions use one normalization path and remain ordinary draft edits. |
| [`Composer.move`](../../web/src/features/prompts/Composer.tsx#L86) | Reorder one block within its existing placement. | Copy array, check neighbor exists in same slot, swap, then update. | Movement is explicit and immutable; crossing history/input requires the visible placement selector. |
| [`Composer.add`](../../web/src/features/prompts/Composer.tsx#L93) | Append a new editable instruction. | Create a random Block ID, empty system template/default filters/variables, before_history placement, then normalize through update. | New instructions have clear defaults and an independent source identity; no hidden preset creation occurs behind a plus icon. |
| [`SkillPrompt`](../../web/src/features/prompts/Composer.tsx#L422) | Select and inspect one discovered procedure. | Show metadata/body-inclusion state; query by project/name/hash only on selection, mention or opening; expose a checkbox and literal source. | Catalog descriptions remain lightweight while explicit bodies are visible at their actual context location. |
| [`BlockEditor`](../../web/src/features/prompts/Composer.tsx#L476) | Edit one block and stage advanced rule JSON. | Controlled basic fields call parent onChange; advanced text has source baseline plus Apply parsing/allowed-key validation and error state; role options exclude tool. | Malformed JSON does not corrupt the saved draft, template typing coalesces by field, and applied/undo changes reset the rule baseline without losing unrelated typed text. |

New/Duplicate callbacks create a new preset ID with revision 0; duplicate keeps block content/IDs inside the new aggregate, which is valid because block identity uniqueness is per preset. Rename edits the selected aggregate's name. Block field callbacks carry edit groups `name:<id>` / `template:<id>` for typing, whereas enabling/role/placement/move/delete are structural steps. Apply rules accepts only variables, condition, provider_allowlist, and model_allowlist. The frontend checks JSON shape/keys; Pydantic/compiler enforce the actual field and condition grammar. A block's source-based rule draft follows applied changes/undo rather than blindly overwriting JSON on every template keystroke.

## web/src/features/history/ConversationHistory.tsx

**Layer: Frontend presentation / conversation organization.** Displays active/archive search, flat folder groups, latest-state menus, and revision-bound metadata/copy dialogs.

**Connections and effects:** Receives bootstrap sessions/folders and parent busy/error/action callbacks. Only the fork dialog fetches additional succeeded Run choices. Menus/dialogs use createPortal to avoid clipping by the sidebar scroll container.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`ConversationHistory`](../../web/src/features/history/ConversationHistory.tsx#L36) | Render searchable grouped history and actions. | Derive menus and dialogs from latest session/folder props while retaining typed operation/value drafts and modal focus. | Run completion can advance revisions while a dialog remains open; metadata updates must use current observed identity. |
| [`ConversationHistory.openDialog`](../../web/src/features/history/ConversationHistory.tsx#L108) | Switch from menu to an explicit input/confirmation dialog. | Clear menu, set initial form value, store typed dialog variant. | Rename/move/fork/delete get distinct fields and consequences rather than overloading one ambiguous button. |
| [`ConversationHistory.openMenu`](../../web/src/features/history/ConversationHistory.tsx#L113) | Position the action menu for a chosen row/folder. | Read trigger bounding box and clamp left/top within viewport before saving target. | Portal rendering can be outside scroll clipping while remaining near the user's clicked control. |
| [`ConversationHistory.update`](../../web/src/features/history/ConversationHistory.tsx#L121) | Submit one revisioned conversation metadata patch. | PATCH session ID with expected_revision and requested changes. | Server revision/archive/folder constraints remain authoritative; UI does not mutate saved messages. |
| [`ConversationHistory.copy`](../../web/src/features/history/ConversationHistory.tsx#L127) | Copy history and navigate to the independent result. | POST observed revision/operation/boundary, refresh and explicitly choose returned session/project IDs. | Navigation cannot retain a stale root after metadata changes; evidence copies still replay no effects. |
| [`ConversationHistory.submit`](../../web/src/features/history/ConversationHistory.tsx#L137) | Execute the current dialog's operation. | Branch by typed dialog kind for folder create/rename/delete, session rename/move/delete/fork; call update/copy/request, notify removal/refresh, close only after success. | One form handles shared busy/error presentation without hiding different backend semantics. Failed/stale actions leave input available for correction. |
| [`ConversationHistory.rows`](../../web/src/features/history/ConversationHistory.tsx#L162) | Render selectable conversation rows and menu triggers. | Map IDs/titles/selection/active reference into buttons with accessibility labels and activity markers. | Navigation uses stable identities and separates selection from metadata actions. |

Inline archive/restore callbacks close the menu, submit the latest observed revision, refresh, and select Active after restore. Copy/archive/delete are disabled while active; rename/move remain available through versioned metadata checks. Fork dialogs list only succeeded Runs and default to the latest successful boundary. Folder deletion explicitly explains that members move to Unfiled. Conversation deletion explains that owned traces/Runs are removed; the backend also cleans checkpoints. Form submit prevents browser navigation and awaits the parent's perform wrapper. Escape/backdrop/close handlers only dismiss local controls; they do not undo completed API effects.

## web/src/features/inspection/Inspector.tsx

**Layer: Frontend presentation / execution evidence.** Shows optional compiled Preview and saved model/tool receipts without transforming stored evidence.

**Connections and effects:** Reads selected Run trace through query key including raw flag; changes tabs only in parent state. Export is a direct local API download. Rendering is literal text, never evaluation of provider/tool output.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`Inspector`](../../web/src/features/inspection/Inspector.tsx#L8) | Inspect selected frozen input or actual saved calls. | Show message roles/sources/activation/prepared schemas for Preview; query redacted/raw receipts for Run, show actual body/metadata/usage/transforms/results/attempt history/copy provenance. | Canonical, prepared, and wire bodies remain separately labeled. Unknown metrics display as unknown, copied usage is not described as newly incurred, and original raw access is explicit. |

## web/src/features/settings/Settings.tsx

**Layer: Frontend presentation / versioned settings editor.** Edits one StudioConfig aggregate including model endpoint/reference, sandbox mode, local permissions, MCP connections, and limits.

**Connections and effects:** Local draft is initialized from bootstrap. Saving goes through /config revision validation; optional discovery occurs after save, then parent refreshes saved projections and live inspection context. Inputs contain references rather than secret values.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`Settings`](../../web/src/features/settings/Settings.tsx#L8) | Edit the one versioned execution configuration. | Keep local general/connection/tool drafts, show the shared searchable ToolCatalog with effective read-only badges, save config with its observed revision, and optionally discover configured MCP servers. | Settings owns policy edits; task presets and ToolCatalog views cannot silently change permissions. |
| [`Settings.setProvider`](../../web/src/features/settings/Settings.tsx#L26) | Patch provider fields inside the draft. | Replace draft and shallow-merge provider options. | Endpoint/model/reference changes remain local until a versioned save; other aggregate fields stay intact. |
| [`Settings.save`](../../web/src/features/settings/Settings.tsx#L28) | Save settings and optionally discover their MCP tools. | Set busy/clear feedback, PUT current revision, update draft from response, optionally POST refresh, call onSaved, report result, and always clear busy. | Discovery uses saved definitions, and newly discovered tools still require explicit permission. Errors do not silently switch to simulation. |
| [`Settings.change`](../../web/src/features/settings/Settings.tsx#L300) | Patch one MCP server card in the local draft. | In the map closure, replace only the server at its index with merged fields. | A server edit changes the aggregate consistently without independent child persistence or model-controlled connection loading. |

Server arguments are split by newlines into an argument array. Stdio environment editor splits each nonempty NAME=SERVER_VARIABLE line at the first equals sign on blur; malformed/missing identifiers are rejected by the backend. HTTP credentials use a variable-name field, never a key input. Tool permission changes replace the map entry with allow/ask/deny; unavailable names can be removed explicitly. New server callbacks create a random ID and blank transport fields; removal only edits the draft until Save. Environment provider overrides are applied at backend startup, while saved UI values apply immediately to later Runs until restart.

## web/src/features/workspace/WorkspacePanel.tsx

**Layer: Frontend presentation / workspace context editor.** Inspects actual OS isolation and edits file-backed memory/procedures with hash-bound saves.

**Connections and effects:** Reads /workspace; full skills load only on click. Parent owns next-Run skill names and live context refresh. Changing file content affects new initial snapshots; it cannot rewrite existing Run context.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`WorkspacePanel`](../../web/src/features/workspace/WorkspacePanel.tsx#L15) | Inspect and edit the selected project files. | Use project-keyed queries and root-addressed requests for memory/skills/probes; retain success feedback above hash-keyed child editors. | File writes cannot silently target the default project, and successful saves remain visible after editor remount. |
| [`WorkspacePanel.changed`](../../web/src/features/workspace/WorkspacePanel.tsx#L37) | Refresh context after an accepted file edit. | Notify the parent to refresh bootstrap/project context, then await the project-keyed workspace query. | New hashes/catalog entries update editors and live inspection while previous valid previews and historical snapshots remain intact. |
| [`MemoryEditor`](../../web/src/features/workspace/WorkspacePanel.tsx#L192) | Hold and edit the last-read note. | Initialize content from MemoryDocument, disable unchanged saves, call save, and show feedback; parent keys editor by hash. | New server content resets the last-read baseline intentionally, and editing notes does not auto-run the model. |
| [`MemoryEditor.save`](../../web/src/features/workspace/WorkspacePanel.tsx#L204) | Replace memory with its observed hash. | PUT content/expected_hash, notify onSaved, show success or error, and clear busy. | Concurrent file edits are explicit conflicts; the UI cannot erase another Run's memory update silently. |
| [`SkillEditor`](../../web/src/features/workspace/WorkspacePanel.tsx#L248) | Edit/create only the SKILL.md fields required by the format. | Initialize metadata/body with frontmatter removed, prevent renaming existing identity, disable bundled source editing, and collect save feedback. | Users author a focused procedure while the backend owns YAML framing and protections. |
| [`SkillEditor.save`](../../web/src/features/workspace/WorkspacePanel.tsx#L264) | Persist a procedure through the common authoring endpoint. | PUT name/description/instructions/optional existing hash, await onSaved with returned parsed SkillDocument, handle error/busy. | Skill-creator/native tool/UI agree on the actual file source, and updates use the fingerprint loaded for inspection. |

## Type contract and stylesheet modules

[api/generated.ts](../../web/src/api/generated.ts) is generated from exported FastAPI OpenAPI and contains type declarations, not runtime functions. [scripts/export_api.py](../../scripts/export_api.py) injects RunEvent schema for the SSE envelope before openapi-typescript generates the checked-in contract. [api/client.ts](../../web/src/api/client.ts) selects aliases such as Preset-Output/StudioConfig-Output; this accounts for defaulted fields in output schemas. The browser still trusts a validated backend response; generated types are not a runtime JSON validator.

[style.css](../../web/src/style.css) owns visual hierarchy, grid/flex sizing, scroll regions, focus/status states, and responsive view composition. It contains no functions. The final cascade is authoritative; some earlier rules are refined by later conversation/workbench rules.

| Region / selectors | Fixed versus scrollable behavior | Design reason |
| --- | --- | --- |
| studio / sidebar / workspace | App fills viewport and bounds overflow; sidebar uses heading/actions/controls/list/footer areas. | Workspace software keeps its navigation and task controls reachable. |
| history-controls / session-list / sidebar-footer | Search/filter stay above the scrolling history; footer stays below. | Folder rows do not push Settings/Workspace out of view. |
| topbar / conversation-toolbar | Conversation title/status/pane buttons and turn selection remain above history. | Inspection/navigation do not depend on scrolling back to the top. |
| conversation-scroll | Transcript scrolls independently with contained overscroll, styled scrollbars, bottom following logic. | User can read old evidence without streamed text continually moving their view. |
| task-dock / task-composer / archived-dock | Message input/actions sit below the transcript; archive replaces submission with Restore. | Run/Stop remain operationally obvious and archive state has an explicit action. |
| tools-content / tool-filter / panel-footer | Tool results scroll with a sticky search/visibility filter, fixed heading and permission action. | A longer tool catalog retains reachable navigation and makes effective exposure inspectable. |
| task-plan / patch-file | Plans and patch previews stay inside their owning transcript turn and can be folded. | Progress and concrete changes remain connected to recorded evidence. |
| utility-pane / panel-heading / composer-controls / block-list / panel-footer | Optional side pane has fixed heading/preset/actions/save areas and scrolling block content. | A long preset does not hide selection or undo/save controls. |
| inspector-content / workspace-content | Evidence/file body has its own bounded scroll region. | Tool JSON and memory text cannot stretch the entire application viewport. |
| menu-layer / history-menu / modal-backdrop | Portal menus and modal dialogs overlay bounded panels with deliberate dismiss controls. | Sidebar overflow cannot clip actions; destructive choices show consequences. |

At width ≤1100px, workbench becomes a single grid; the utility pane occupies the toolbar/history area while the task dock remains in the bottom row. At ≤760px, the sidebar is a 240px fixed overlay with backdrop, topbar wraps, and controls/transcript margins compress. There is no separate mobile execution workflow. API, history, and state ownership stay the same.

## Inline callback inventory

Named/nested component functions are individually documented above. Local JSX handlers, query/effect callbacks, returned hook commands, and array predicates are listed with exact source locations in [Callback reference](callbacks.md). They belong to their owning frontend component/transport layer; the parent sections here explain the full operation they compose.

## web/src/features/tools/ToolCatalog.tsx

**Layer: Frontend presentation / concrete tool inspection.** Shows grouped tool schemas and effective current exposure in Tools and Settings.

**Connections and effects:** Uses bootstrap ToolDefinition/config projections only; optional callbacks edit the owning Settings draft. It never executes a tool or saves permissions on render.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`toolGroup`](../../web/src/features/tools/ToolCatalog.tsx#L36) | Select a concrete capability group. | Match native names in fixed local groups, classify server_id tools as MCP, otherwise return Other tools. | Presentation does not infer authority from tool descriptions or external read-only annotations. |
| [`ToolCatalog`](../../web/src/features/tools/ToolCatalog.tsx#L44) | Render searchable grouped tool definitions. | Filter names/descriptions by text and exposure, show effective policy badges, expandable JSON schemas and optional draft permission selectors. | Users can inspect what the model receives and why a tool is blocked without a separate configuration store. |
| [`ToolCatalog.exposed`](../../web/src/features/tools/ToolCatalog.tsx#L55) | Calculate the displayed effective exposure. | Require allow/ask in current config and omit native mutation names when mode is read-only. | UI mirrors backend selected/dispatch behavior; it remains a display predicate rather than authorization. |
| [`ToolsPanel`](../../web/src/features/tools/ToolCatalog.tsx#L148) | Place the catalog in the workbench. | Render fixed heading/close/footer, bounded scrolling catalog and an Edit permissions action to Settings. | The tool list is usable alongside chat without an unrecorded manual execution route. |

## web/src/features/chat/TaskPlan.tsx

**Layer: Frontend presentation / recorded task progress.** Renders an optional plan already derived by Studio.view.

**Connections and effects:** RunTurn supplies validated RunView.plan; no local API write, polling channel, database record or independent completion claim exists.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`TaskPlan`](../../web/src/features/chat/TaskPlan.tsx#L5) | Display the latest recorded agent plan. | Render a foldable explanation, completed/total count and per-item pending/in_progress/completed markers. | Progress stays beside its Run and survives refresh/copies through paired tool receipts; completed labels do not substitute for actual check evidence. |

### Added browser acceptance

The [tool/preset/plan browser scenario](../../web/e2e/studio.spec.ts#L22) searches the Tools catalog, expands a schema, saves read-only Settings, confirms effective patch blocking, selects and expands the Coding focus, inspects empty-task environment/tool input, directly runs update_plan and reloads the recorded progress. Its finally block restores write mode in the isolated browser database. Existing browser cases retain their original history/approval/project/live-preview coverage.

## web/src/features/chat/ContextPanel.tsx

**Layer: Frontend presentation / explicit history selection.** Displays original/effective history estimates, derived summary provenance and explicit Compact/Restore/Stop actions.

**Connections and effects:** Uses typed API/query state; it never summarizes locally, rewrites chat or auto-compacts on an estimate. Session/config revisions are captured in commands.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`ContextPanel`](../../web/src/features/chat/ContextPanel.tsx#L10) | Render history accounting and summary records. | Query the session context, poll only while compacting, show history-only estimates/usage boundaries and enable idle versioned actions. | Original chat remains visible while users can inspect and change future context deliberately. |
| [`ContextPanel.action`](../../web/src/features/chat/ContextPanel.tsx#L33) | Sequence one explicit context lifecycle action. | Set busy/error, await API command, refetch context and refresh parent session/config, then release controls. | Server revisions remain authoritative and failures stay visible without silently clearing valid summaries. |

## web/src/features/chat/ProcessesPanel.tsx

**Layer: Frontend presentation / native lifecycle control.** Shows original owned background command output and explicit pipe/EOF/Stop actions.

**Connections and effects:** Typed session-process APIs and conditional polling provide projections; the panel cannot launch arbitrary host commands. Fixed heading and bounded output/scroll areas use existing workbench layout.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`ProcessesPanel`](../../web/src/features/chat/ProcessesPanel.tsx#L11) | Render actual owned command progress. | Query/poll while Runs/processes are active, display bounded streams/PID/mode/results, track per-process input drafts and disable closed/busy input. | Long-lived work stays inspectable and controllable with separate native process and conversation ownership. |
| [`ProcessesPanel.act`](../../web/src/features/chat/ProcessesPanel.tsx#L24) | Send one explicit pipe or stop command. | Post original session/process IDs, clear an accepted input draft, refetch saved status and show failures. | A Stop button awaits server cleanup; frontend state does not invent success or replay effects. |

## web/src/shared/PackageActions.tsx

**Layer: Frontend presentation / reusable data transfer.** Shares bounded JSON import/export controls between Prompt and Workspace Skills.

**Connections and effects:** Parents supply concrete API/editor callbacks. Hidden file input and a local download Blob transfer data only; no browser credentials or package code execution is introduced.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`PackageActions`](../../web/src/shared/PackageActions.tsx#L6) | Render explicit bounded package transfer. | Maintain selected file reference/busy/error state and show only available export plus import actions. | Reuse remains a small data workflow rather than a separate package-management service. |
| [`PackageActions.save`](../../web/src/shared/PackageActions.tsx#L23) | Download current reusable data. | Await the parent's package callback, encode JSON into a Blob, trigger a filename download and revoke the object URL. | Unsaved prompt data can be exported without saving configuration or including Run history. |
| [`PackageActions.load`](../../web/src/shared/PackageActions.tsx#L43) | Import one user-selected JSON file. | Enforce a 2 MiB browser input limit, parse JSON, await authoritative parent/API validation and clear the file input in finally. | Failed imports remain visible and the same corrected file can be selected again. |

## web/src/features/chat/ConversationTurn.tsx

**Layer: Frontend presentation / recorded execution.** Renders persisted turns, tool evidence and explicit recovery decisions extracted from App.

**Connections and effects:** Receives typed RunView and action callbacks from Workspace. Rendering and component-local disclosure/recovery input do not select projects, issue model calls, mutate snapshots or invent tool receipts.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`RunTurn`](../../web/src/features/chat/ConversationTurn.tsx#L14) | Render one persisted execution turn. | Display status/provenance, paired assistant/tool messages, usage and an optional validated recorded TaskPlan. | Continuous chat keeps model claims and actual receipts connected to their Run; progress derives from evidence and survives reload. |
| [`ToolOutput`](../../web/src/features/chat/ConversationTurn.tsx#L199) | Render an inspectable tool receipt. | Parse JSON when possible; show command reports, patch operations/bounded diffs, ranged-read metadata/content or a folded general result. | Effects and evidence remain readable without an additional execution endpoint. |
| [`Recovery`](../../web/src/features/chat/ConversationTurn.tsx#L266) | Collect explicit decisions for uncertain calls. | Maintain receipt-ID choices, permit tool record or retry, collect supplied result text and require all unknown calls to be resolved before resume. | Model text cannot resolve its own uncertainty or authorize repeated effects. |

## web/src/features/projects/DirectoryField.tsx

**Layer: Frontend presentation / desktop selection.** Collects a workspace folder through the OS before offering manual entry.

**Connections and effects:** Uses the typed folder-picker HTTP response and an AbortController; Projects owns the name/path form and registration. Does not place a filesystem handle or path in browser storage.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`DirectoryField`](../../web/src/features/projects/DirectoryField.tsx#L6) | Show the selected folder and native Choose/Change action. | Keep picker busy/fallback/notice state; render a path output normally and an input only after unavailability; abort an owned request on unmount. | The normal local workflow is visual selection, cancellation preserves user input, and unsupported desktops remain usable. |
| [`DirectoryField.browse`](../../web/src/features/projects/DirectoryField.tsx#L25) | Request one explicit system folder selection. | Mark busy, POST current/initial path with a signal, update only on selected, expose manual fallback on unavailable/transport failure, distinguish busy/cancel and always clear ownership. | A competing dialog is not a reason to bypass selection; user cancellation cannot wipe a prior valid path. |

## web/src/shared/useDialogFocus.ts

**Layer: Frontend presentation / keyboard interaction.** Shares a small concrete modal focus lifecycle for Settings, Projects and history actions.

**Connections and effects:** Uses effects and DOM focus only; callers own modal open state and close/save restrictions. One effect captures/restores previous focus; another updates keyboard handling when close behavior changes.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`useDialogFocus`](../../web/src/shared/useDialogFocus.ts#L4) | Focus an open dialog and restore the prior connected control when it closes. | Prefer an input/select/textarea then button; capture the original active element; attach/remove a document key listener while open. | Keyboard users can finish forms without reaching unrelated background controls or losing their place. |
| [`useDialogFocus.handle`](../../web/src/shared/useDialogFocus.ts#L27) | Handle Escape and wrap modal Tab navigation. | Invoke the caller's close callback, or filter visible enabled tabbable elements and wrap first/last focus; focus the dialog when no controls exist. | Focus behavior respects caller busy state and the actual rendered controls instead of a hard-coded button list. |

## web/src/shared/Json.tsx

**Layer: Frontend shared presentation.** Renders JSON evidence shared by chat and inspection without a cross-feature implementation dependency.

**Connections and effects:** Receives unknown data; formats literal text in a pre element. It owns no query, permission, provider call or execution state.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`Json`](../../web/src/shared/Json.tsx#L2) | Render structured evidence as inert formatted text. | JSON.stringify with two-space indentation inside pre. | Chat and Inspector share presentation without importing an inspection feature or executing returned HTML. |
