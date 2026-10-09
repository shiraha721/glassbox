import { useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Copy,
  Pencil,
  Plus,
  Redo2,
  Save,
  Trash2,
  Undo2,
  X,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { PackageActions } from '../../shared/PackageActions';
import {
  errorMessage,
  request,
  workspacePath,
  type Block,
  type Preset,
  type WorkspaceView,
  type SkillInfo,
  type SkillDocument,
} from '../../api/client';

type Props = {
  preset: Preset;
  presets: Preset[];
  dirty: boolean;
  busy: boolean;
  onChange: (preset: Preset, group?: string) => void;
  onSelect: (preset: Preset) => void;
  onSave: () => void;
  onClose?: () => void;
  onUndo?: () => void;
  onRedo?: () => void;
  onDiscard?: () => void;
  canUndo?: boolean;
  canRedo?: boolean;
  projectId: string;
  workspace?: WorkspaceView;
  workspaceError?: string;
  selectedSkills: string[];
  input: string;
  onSkills: (names: string[]) => void;
  onWorkspace: () => void;
  onRefreshContext: () => void;
  onImported?: (preset: Preset) => Promise<void>;
};

/** Keep visual ordering aligned with the compiler's two insertion slots. */
function ordered(blocks: Block[]): Block[] {
  return [...blocks]
    .sort((a, b) => Number(a.placement === 'before_input') - Number(b.placement === 'before_input'))
    .map((block, index) => ({ ...block, order: (index + 1) * 10 }));
}

/** Edit preset blocks beside the file context in the compiler's actual message order. */
export function Composer({
  preset,
  presets,
  dirty,
  busy,
  onChange,
  onSelect,
  onSave,
  onClose,
  onUndo,
  onRedo,
  onDiscard,
  canUndo,
  canRedo,
  projectId,
  workspace,
  workspaceError,
  selectedSkills,
  input,
  onSkills,
  onWorkspace,
  onRefreshContext,
  onImported,
}: Props) {
  const [renamingId, setRenamingId] = useState('');
  const update = (blocks: Block[]) => onChange({ ...preset, blocks: ordered(blocks) });
  const move = (index: number, direction: number) => {
    const blocks = [...preset.blocks];
    const other = index + direction;
    if (!blocks[other] || blocks[index].placement !== blocks[other].placement) return;
    [blocks[index], blocks[other]] = [blocks[other], blocks[index]];
    update(blocks);
  };
  const add = () =>
    update([
      ...preset.blocks,
      {
        id: crypto.randomUUID(),
        name: 'New instruction',
        role: 'system',
        template: '',
        enabled: true,
        placement: 'before_history',
        order: 0,
        variables: {},
        provider_allowlist: null,
        model_allowlist: null,
        condition: null,
      },
    ]);
  return (
    <aside className="composer panel" aria-label="Prompt composer">
      <div className="panel-heading">
        <h2>Prompt composer</h2>
        <span className="count">{preset.blocks.length}</span>
        {onClose && (
          <button className="icon-button" aria-label="Close prompt" onClick={onClose}>
            <X size={16} />
          </button>
        )}
      </div>
      <div className="composer-controls">
        <div className="preset-picker">
          <div className="row-between">
            <label htmlFor="preset">Active preset</label>
            <span className={`draft-status ${dirty ? 'unsaved' : ''}`}>
              {dirty ? 'Unsaved' : 'Saved'}
            </span>
          </div>
          <div className="row">
            <select
              id="preset"
              value={preset.id}
              disabled={busy}
              onChange={(event) => {
                const next = presets.find((item) => item.id === event.target.value);
                if (next) onSelect(next);
              }}
            >
              {(presets.some((item) => item.id === preset.id) ? presets : [...presets, preset]).map(
                (item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                    {item.revision === 0 ? ' (draft)' : ''}
                  </option>
                ),
              )}
            </select>
          </div>
          <div className="preset-actions">
            <button
              className="button small"
              disabled={busy}
              onClick={() =>
                onChange({
                  id: crypto.randomUUID(),
                  name: 'Untitled preset',
                  revision: 0,
                  blocks: [],
                })
              }
            >
              <Plus size={13} /> New
            </button>
            <button
              className="button small"
              aria-label="Duplicate preset"
              disabled={busy}
              onClick={() =>
                onChange({
                  ...preset,
                  id: crypto.randomUUID(),
                  name: `${preset.name.slice(0, 115)} copy`,
                  revision: 0,
                })
              }
            >
              <Copy size={13} /> Duplicate
            </button>
            <button
              className="button small"
              aria-label="Rename preset"
              disabled={busy}
              aria-pressed={renamingId === preset.id}
              onClick={() => setRenamingId(renamingId === preset.id ? '' : preset.id)}
            >
              <Pencil size={13} /> Rename
            </button>
          </div>
          {(renamingId === preset.id || preset.revision === 0) && (
            <label htmlFor="preset-name">
              Preset name
              <input
                id="preset-name"
                disabled={busy}
                value={preset.name}
                maxLength={120}
                onChange={(event) =>
                  onChange({ ...preset, name: event.target.value }, 'preset-name')
                }
              />
            </label>
          )}
          <div className="subtle row-between">
            <span>{preset.blocks.filter((block) => block.enabled).length} enabled blocks</span>
            <div className="row tight">
              <button
                className="icon-button"
                aria-label="Undo preset edit"
                title="Undo"
                disabled={busy || !canUndo}
                onClick={onUndo}
              >
                <Undo2 size={15} />
              </button>
              <button
                className="icon-button"
                aria-label="Redo preset edit"
                title="Redo"
                disabled={busy || !canRedo}
                onClick={onRedo}
              >
                <Redo2 size={15} />
              </button>
            </div>
          </div>
        </div>
        <div className="block-list">
          {onImported && (
            <PackageActions
              kind="preset"
              filename={`${preset.id}.preset.json`}
              disabled={busy}
              exportPackage={async () => ({ format: 'glassbox.preset.v1', preset })}
              importPackage={async (value) => {
                await onImported(await request<Preset>('/presets/import', 'POST', value));
              }}
            />
          )}
          {(['before_history', 'before_input'] as const).map((placement) => (
            <section
              className="composer-slot"
              key={placement}
              aria-label={
                placement === 'before_history' ? 'Before conversation' : 'Before your message'
              }
            >
              <div className="slot-heading">
                <h3>
                  {placement === 'before_history' ? 'Before conversation' : 'Before your message'}
                </h3>
                <span className="count">
                  {preset.blocks.filter((block) => block.placement === placement).length}
                </span>
              </div>
              <p className="subtle slot-description">
                {placement === 'before_history'
                  ? 'Preset instructions, followed by workspace files and conversation history.'
                  : 'Additional context, followed by your next message.'}
              </p>
              {preset.blocks.map(
                (block, index) =>
                  block.placement === placement && (
                    <BlockEditor
                      key={block.id}
                      block={block}
                      index={index}
                      busy={busy}
                      canUp={index > 0 && preset.blocks[index - 1].placement === block.placement}
                      canDown={
                        index < preset.blocks.length - 1 &&
                        preset.blocks[index + 1].placement === block.placement
                      }
                      onMove={(direction) => move(index, direction)}
                      onDelete={() => update(preset.blocks.filter((item) => item.id !== block.id))}
                      onChange={(next, group) =>
                        onChange(
                          {
                            ...preset,
                            blocks: ordered(
                              preset.blocks.map((item) => (item.id === block.id ? next : item)),
                            ),
                          },
                          group,
                        )
                      }
                    />
                  ),
              )}
              {!preset.blocks.some((block) => block.placement === placement) && (
                <div className="slot-empty">No blocks in this position</div>
              )}
              {placement === 'before_history' && (
                <>
                  <section className="prompt-context" aria-label="Workspace prompt context">
                    <div className="slot-heading">
                      <h3>Workspace context</h3>
                      <button className="text-button" onClick={onRefreshContext}>
                        Refresh files
                      </button>
                    </div>
                    <p className="subtle">
                      File sources · captured once for each Run · system messages
                    </p>
                    {workspaceError && (
                      <p role="alert" className="error-text">
                        {workspaceError}
                      </p>
                    )}
                    {!workspace && !workspaceError && (
                      <p className="subtle">Loading workspace files…</p>
                    )}
                    {workspace && (
                      <>
                        <details className="context-file">
                          <summary>
                            <strong>{workspace.instructions?.path || 'AGENTS.md'}</strong>
                            <span className="badge">
                              {workspace.instructions ? 'Included' : 'Not found'}
                            </span>
                          </summary>
                          <div className="context-file-body">
                            <p className="subtle">
                              workspace:{workspace.instructions?.path || 'AGENTS.md'} · root
                              instructions · literal file content
                            </p>
                            {workspace.instructions ? (
                              <pre>{workspace.instructions.content || '(Empty file)'}</pre>
                            ) : (
                              <p className="subtle">
                                Create AGENTS.md in the workspace root to add project instructions.
                              </p>
                            )}
                          </div>
                        </details>
                        <details className="context-file">
                          <summary>
                            <strong>Workspace memory</strong>
                            <span className="badge">
                              {workspace.memory.content ? 'Included' : 'Empty'}
                            </span>
                          </summary>
                          <div className="context-file-body">
                            <p className="subtle">workspace:memory · {workspace.memory.path}</p>
                            <pre>{workspace.memory.content || '(No saved notes)'}</pre>
                            <button className="text-button" onClick={onWorkspace}>
                              Edit memory in Workspace
                            </button>
                          </div>
                        </details>
                        <details className="context-file" open>
                          <summary>
                            <strong>Skill catalog</strong>
                            <span className="badge">{workspace.skills.length} available</span>
                          </summary>
                          <div className="context-file-body">
                            <p className="subtle">
                              workspace:skills · descriptions are included; bodies load when
                              selected, mentioned, or requested by the agent.
                            </p>
                            {workspace.skills.map((skill) => (
                              <SkillPrompt
                                key={skill.name}
                                projectId={projectId}
                                skill={skill}
                                selected={selectedSkills.includes(skill.name)}
                                mentioned={new RegExp(
                                  `(^|\\s)\\$${skill.name}(?=\\s|$|[.,!?])`,
                                ).test(input)}
                                busy={busy}
                                onSelect={(checked) =>
                                  onSkills(
                                    checked
                                      ? [...selectedSkills, skill.name]
                                      : selectedSkills.filter((name) => name !== skill.name),
                                  )
                                }
                              />
                            ))}
                            {Object.entries(workspace.skill_errors).map(([name, message]) => (
                              <p className="error-text" key={name}>
                                {name}: {message}
                              </p>
                            ))}
                            <button className="text-button" onClick={onWorkspace}>
                              Create or edit Skills in Workspace
                            </button>
                          </div>
                        </details>
                      </>
                    )}
                  </section>
                  <div className="history-boundary">Conversation history</div>
                </>
              )}
            </section>
          ))}
          <button className="add-block" disabled={busy} onClick={add}>
            <Plus size={15} />
            Add message block
          </button>
          <div className="history-boundary">Your message · inserted once</div>
        </div>
        <div className="panel-footer">
          <button className="button" disabled={!dirty || busy} onClick={onDiscard}>
            Discard changes
          </button>
          <button
            className="button primary"
            disabled={!dirty || busy || !preset.name.trim()}
            onClick={onSave}
          >
            <Save size={14} />
            Save preset
          </button>
        </div>
      </div>
    </aside>
  );
}

/** Select a file-backed skill and load its body only when included or opened for inspection. */
function SkillPrompt({
  projectId,
  skill,
  selected,
  mentioned,
  busy,
  onSelect,
}: {
  projectId: string;
  skill: SkillInfo;
  selected: boolean;
  mentioned: boolean;
  busy: boolean;
  onSelect: (checked: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ['skill', projectId, skill.name, skill.hash],
    queryFn: () => request<SkillDocument>(workspacePath(`/skills/${skill.name}`, projectId)),
    enabled: open || selected || mentioned,
  });
  return (
    <div className="prompt-skill">
      <div className="row-between">
        <label className="checkbox-label">
          <input
            type="checkbox"
            aria-label={`Include ${skill.name} in prompt`}
            checked={selected || mentioned}
            disabled={busy || mentioned}
            onChange={(event) => onSelect(event.target.checked)}
          />
          {skill.name}
        </label>
        <span className="subtle">
          {mentioned ? 'Mentioned in task' : selected ? 'Body included' : 'On demand'}
        </span>
      </div>
      <p className="subtle">{skill.description}</p>
      <details onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>{skill.path} · view source</summary>
        {query.isPending && <p className="subtle">Loading skill…</p>}
        {query.isError && (
          <p role="alert" className="error-text">
            {query.error.message}
          </p>
        )}
        {query.data && <pre>{query.data.content}</pre>}
      </details>
    </div>
  );
}

/** Keep advanced conditions explicit JSON; the server validates their restricted grammar. */
function BlockEditor({
  block,
  index,
  busy,
  canUp,
  canDown,
  onMove,
  onDelete,
  onChange,
}: {
  block: Block;
  index: number;
  busy: boolean;
  canUp: boolean;
  canDown: boolean;
  onMove: (direction: number) => void;
  onDelete: () => void;
  onChange: (block: Block, group?: string) => void;
}) {
  const source = JSON.stringify(
    {
      variables: block.variables,
      condition: block.condition,
      provider_allowlist: block.provider_allowlist,
      model_allowlist: block.model_allowlist,
    },
    null,
    2,
  );
  const [ruleDraft, setRuleDraft] = useState({ source, text: source });
  // An applied change or undo replaces the rule baseline; ordinary template edits do not.
  const rules = ruleDraft.source === source ? ruleDraft.text : source;
  const [error, setError] = useState('');
  return (
    <details
      className={`block ${block.enabled ? '' : 'disabled'}`}
      open={index === 0 || !block.template}
    >
      <summary>
        <span className="block-number">{String(index + 1).padStart(2, '0')}</span>
        <span className="block-name">{block.name || 'Untitled'}</span>
        <span className={`role role-${block.role}`}>{block.role}</span>
      </summary>
      <fieldset className="block-body" disabled={busy}>
        <div className="row-between">
          <label className="checkbox-label">
            <input
              type="checkbox"
              aria-label={`Enable ${block.name}`}
              checked={block.enabled}
              onChange={(event) => onChange({ ...block, enabled: event.target.checked })}
            />
            Enabled
          </label>
          <div className="row tight">
            <button
              className="icon-button"
              aria-label={`Move ${block.name} up`}
              disabled={!canUp}
              onClick={() => onMove(-1)}
            >
              <ArrowUp size={13} />
            </button>
            <button
              className="icon-button"
              aria-label={`Move ${block.name} down`}
              disabled={!canDown}
              onClick={() => onMove(1)}
            >
              <ArrowDown size={13} />
            </button>
            <button className="icon-button" aria-label={`Remove ${block.name}`} onClick={onDelete}>
              <Trash2 size={13} />
            </button>
          </div>
        </div>
        <label>
          Block name
          <input
            aria-label={`Block ${index + 1} name`}
            value={block.name}
            onChange={(event) =>
              onChange({ ...block, name: event.target.value }, `name:${block.id}`)
            }
          />
        </label>
        <div className="row">
          <label>
            Role
            <select
              aria-label={`${block.name} role`}
              value={block.role}
              onChange={(event) =>
                onChange({ ...block, role: event.target.value as Block['role'] })
              }
            >
              <option value="system">System</option>
              <option value="user">User</option>
              <option value="assistant">Assistant</option>
            </select>
          </label>
          <label>
            Position
            <select
              aria-label={`${block.name} placement`}
              value={block.placement}
              onChange={(event) =>
                onChange({ ...block, placement: event.target.value as Block['placement'] })
              }
            >
              <option value="before_history">Before conversation</option>
              <option value="before_input">Before your message</option>
            </select>
          </label>
        </div>
        <label>
          Message template
          <textarea
            aria-label={`${block.name} template`}
            rows={6}
            value={block.template}
            onChange={(event) =>
              onChange({ ...block, template: event.target.value }, `template:${block.id}`)
            }
            spellCheck={false}
          />
        </label>
        <details className="advanced">
          <summary>Variables & activation rules</summary>
          <p className="subtle">
            Declare {'{{name}}'} variables here. Use structured conditions with all, any, not, eq,
            in, or exists.
          </p>
          <textarea
            aria-label={`${block.name} rules`}
            rows={9}
            className="code-input"
            value={rules}
            onChange={(event) => setRuleDraft({ source, text: event.target.value })}
            spellCheck={false}
          />
          <button
            className="button small"
            onClick={() => {
              try {
                const parsed = JSON.parse(rules);
                const allowed = ['variables', 'condition', 'provider_allowlist', 'model_allowlist'];
                if (
                  !parsed ||
                  Array.isArray(parsed) ||
                  Object.keys(parsed).some((key) => !allowed.includes(key))
                )
                  throw new Error(
                    'Only variables, condition, provider_allowlist, and model_allowlist are allowed.',
                  );
                onChange({ ...block, ...parsed });
                setError('');
              } catch (caught) {
                setError(errorMessage(caught));
              }
            }}
          >
            Apply rules
          </button>
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
        </details>
      </fieldset>
    </details>
  );
}
