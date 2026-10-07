import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import type { CommandRule, Config } from '../../api/client';

/** Shell command prefixes with their own permission, ahead of the shell tool's default. */
export function CommandRules({
  draft,
  onChange,
}: {
  draft: Config;
  onChange: (config: Config) => void;
}) {
  const [prefix, setPrefix] = useState('');
  const rules = draft.command_rules ?? [];
  const set = (next: CommandRule[]) => onChange({ ...draft, command_rules: next });
  const add = () => {
    const value = prefix.trim().split(/\s+/).join(' ');
    if (!value || rules.some((rule) => rule.prefix === value)) return;
    set([...rules, { prefix: value, permission: 'allow' }]);
    setPrefix('');
  };
  return (
    <section className="settings-card" aria-label="Command rules">
      <h3>Command rules</h3>
      <p className="subtle">
        Give shell commands that start with a prefix their own permission; the longest matching
        prefix wins. Allow and ask rules apply only to single commands without ; | &amp;&amp;,
        redirects or $( ), so a rule for <code>npm test</code> never covers{' '}
        <code>npm test; rm -rf data</code>. Under Ask before changes, allow rules ask instead.
      </p>
      {rules.map((rule, index) => (
        <div className="row" key={rule.prefix}>
          <code className="rule-prefix">{rule.prefix}</code>
          <select
            aria-label={`Permission for ${rule.prefix}`}
            value={rule.permission}
            onChange={(event) =>
              set(
                rules.map((item, i) =>
                  i === index
                    ? { ...item, permission: event.target.value as CommandRule['permission'] }
                    : item,
                ),
              )
            }
          >
            <option value="allow">Allow</option>
            <option value="ask">Ask each time</option>
            <option value="deny">Deny</option>
          </select>
          <button
            className="icon-button"
            aria-label={`Remove rule ${rule.prefix}`}
            onClick={() => set(rules.filter((_, i) => i !== index))}
          >
            <Trash2 size={15} />
          </button>
        </div>
      ))}
      <div className="row">
        <input
          aria-label="New command prefix"
          placeholder="Command prefix, e.g. npm test or git status"
          value={prefix}
          onChange={(event) => setPrefix(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              add();
            }
          }}
        />
        <button className="button" disabled={!prefix.trim()} onClick={add}>
          <Plus size={14} /> Add rule
        </button>
      </div>
    </section>
  );
}
