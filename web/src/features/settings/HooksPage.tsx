import { Plus, Trash2 } from 'lucide-react';
import type { Config, ShellHook } from '../../api/client';

const events: { value: ShellHook['event']; label: string }[] = [
  { value: 'before_tool', label: 'Before a tool runs' },
  { value: 'after_tool', label: 'After a tool runs' },
  { value: 'run_end', label: 'When a run ends' },
];

/** Commands the user runs on this computer at points in a Run, outside the sandbox. */
export function HooksPage({
  draft,
  onChange,
}: {
  draft: Config;
  onChange: (config: Config) => void;
}) {
  const hooks = draft.hooks ?? [];
  const set = (next: ShellHook[]) => onChange({ ...draft, hooks: next });
  const update = (index: number, changes: Partial<ShellHook>) =>
    set(hooks.map((hook, i) => (i === index ? { ...hook, ...changes } : hook)));
  return (
    <>
      <p className="page-intro">
        Hooks run your own commands at points in a run: to log or check tool calls, format files
        after edits, or notify you when a run ends. Each gets the event as JSON on standard input.
      </p>
      <p className="notice warning">
        Hooks run on this computer with your permissions, outside the sandbox, with only basic
        environment variables (PATH, HOME, display). Tool arguments in their input come from the
        model, so treat them as untrusted. Do not put keys in a command; it is saved with the
        settings and each run.
      </p>
      {hooks.map((hook, index) => (
        <section className="settings-card" key={index} aria-label={`Hook ${index + 1}`}>
          <div className="form-grid">
            <label>
              When
              <select
                value={hook.event}
                onChange={(event) =>
                  update(index, { event: event.target.value as ShellHook['event'] })
                }
              >
                {events.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {hook.event !== 'run_end' && (
              <label>
                Tools (pattern)
                <input
                  value={hook.tools}
                  placeholder="* or bash or mcp_*"
                  onChange={(event) => update(index, { tools: event.target.value })}
                />
              </label>
            )}
            <label>
              Timeout (seconds)
              <input
                type="number"
                min="1"
                max="120"
                value={hook.timeout}
                onChange={(event) => update(index, { timeout: Number(event.target.value) })}
              />
            </label>
          </div>
          <label>
            Command
            <input
              className="code-input"
              value={hook.command}
              placeholder="e.g. jq -c . >> ~/agent-tools.log"
              onChange={(event) => update(index, { command: event.target.value })}
            />
          </label>
          <div className="row">
            <button
              className="button small quiet"
              aria-label={`Remove hook ${index + 1}`}
              onClick={() => set(hooks.filter((_, i) => i !== index))}
            >
              <Trash2 size={13} /> Remove
            </button>
          </div>
        </section>
      ))}
      <button
        className="button"
        disabled={hooks.length >= 20}
        onClick={() =>
          set([...hooks, { event: 'before_tool', tools: '*', command: '', timeout: 10 }])
        }
      >
        <Plus size={14} /> Add hook
      </button>
      <p className="subtle">
        Before a tool runs: exit code 0 lets the call run; 2 blocks it and gives the model the
        hook&apos;s standard error as the reason; any other failure or a timeout also blocks it.
        After a tool runs and when a run ends: the result is recorded but never changes the run.
        Processes a hook leaves holding its output are stopped a second after it exits. Each run
        uses the hooks saved when it started.
      </p>
    </>
  );
}
