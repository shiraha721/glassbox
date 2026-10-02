import { useState } from 'react';
import { ArrowDown, ArrowUp, Layers3, Plus, Save, Trash2 } from 'lucide-react';
import { errorMessage, type Block, type Preset } from './api/client';

type Props = {
  preset: Preset;
  presets: Preset[];
  dirty: boolean;
  busy: boolean;
  onChange: (preset: Preset) => void;
  onSelect: (preset: Preset) => void;
  onSave: () => void;
};

/** Keep visual ordering aligned with the compiler's two insertion slots. */
function ordered(blocks: Block[]): Block[] {
  return [...blocks]
    .sort((a, b) => Number(a.placement === 'before_input') - Number(b.placement === 'before_input'))
    .map((block, index) => ({ ...block, order: (index + 1) * 10 }));
}

/** Edit an embedded preset; every edit invalidates the parent workspace's preview. */
export function Composer({ preset, presets, dirty, busy, onChange, onSelect, onSave }: Props) {
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
        <Layers3 size={16} />
        <h2>Prompt composer</h2>
        <span className="count">{preset.blocks.length}</span>
      </div>
      <div className="composer-controls">
        <div className="preset-picker">
          <label htmlFor="preset">PRESET</label>
          <div className="row">
            <select
              id="preset"
              value={preset.revision === 0 ? '' : preset.id}
              disabled={dirty || busy}
              onChange={(event) => {
                const next = presets.find((item) => item.id === event.target.value);
                if (next) onSelect(next);
              }}
            >
              {preset.revision === 0 && <option value="">New preset</option>}
              {presets.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <button
              className="icon-button"
              title="Duplicate preset"
              aria-label="Duplicate preset"
              disabled={busy}
              onClick={() =>
                onChange({
                  ...preset,
                  id: crypto.randomUUID(),
                  name: `${preset.name} copy`,
                  revision: 0,
                })
              }
            >
              <Plus size={15} />
            </button>
          </div>
          <label className="sr-only" htmlFor="preset-name">
            Preset name
          </label>
          <input
            id="preset-name"
            disabled={busy}
            value={preset.name}
            onChange={(event) => onChange({ ...preset, name: event.target.value })}
          />
          <div className="subtle row-between">
            <span>{dirty ? 'Unsaved changes' : `Revision ${preset.revision}`}</span>
            <span>{preset.blocks.filter((block) => block.enabled).length} active</span>
          </div>
        </div>
        <div className="block-list">
          {preset.blocks.map((block, index) => (
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
              onChange={(next) =>
                update(preset.blocks.map((item) => (item.id === block.id ? next : item)))
              }
            />
          ))}
          <button className="add-block" disabled={busy} onClick={add}>
            <Plus size={15} />
            Add message block
          </button>
        </div>
        <div className="panel-footer">
          <button className="button wide" disabled={!dirty || busy} onClick={onSave}>
            <Save size={14} />
            Save preset
          </button>
          <p className="subtle">
            Blocks → history → task → tool results.
            <br />
            Every message keeps its original role.
          </p>
        </div>
      </div>
    </aside>
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
  onChange: (block: Block) => void;
}) {
  const [rules, setRules] = useState(
    JSON.stringify(
      {
        variables: block.variables,
        condition: block.condition,
        provider_allowlist: block.provider_allowlist,
        model_allowlist: block.model_allowlist,
      },
      null,
      2,
    ),
  );
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
        <input
          aria-label={`Block ${index + 1} name`}
          value={block.name}
          onChange={(event) => onChange({ ...block, name: event.target.value })}
        />
        <div className="row">
          <select
            aria-label={`${block.name} role`}
            value={block.role}
            onChange={(event) => onChange({ ...block, role: event.target.value as Block['role'] })}
          >
            <option value="system">System</option>
            <option value="user">User</option>
            <option value="assistant">Assistant</option>
          </select>
          <select
            aria-label={`${block.name} placement`}
            value={block.placement}
            onChange={(event) =>
              onChange({ ...block, placement: event.target.value as Block['placement'] })
            }
          >
            <option value="before_history">Before history</option>
            <option value="before_input">Before task</option>
          </select>
        </div>
        <textarea
          aria-label={`${block.name} template`}
          rows={6}
          value={block.template}
          onChange={(event) => onChange({ ...block, template: event.target.value })}
          spellCheck={false}
        />
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
            onChange={(event) => setRules(event.target.value)}
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
