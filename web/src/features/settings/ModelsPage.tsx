import { useState } from 'react';
import { Plus, RefreshCw, X } from 'lucide-react';
import {
  errorMessage,
  request,
  SIMULATED,
  type Config,
  type ProviderProfile,
} from '../../api/client';
import { effortLabels } from '../composer/choices';

/** Number input that maps an empty field to null ("use the provider's default"). */
function optionalNumber(value: string): number | null {
  return value.trim() ? Number(value) : null;
}

/** One provider's known models, discovery and request options. */
function ProviderModels({
  profile,
  onChange,
}: {
  profile: ProviderProfile;
  onChange: (patch: Partial<ProviderProfile>) => void;
}) {
  const [added, setAdded] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const refresh = async () => {
    setBusy(true);
    setNotice('');
    try {
      const { models } = await request<{ models: string[] }>('/providers/models', 'POST', profile);
      onChange({ models: [...new Set([...profile.models, ...models])].sort() });
      setNotice(`Found ${models.length} models. Save settings to keep them.`);
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  const add = () => {
    const model = added.trim();
    if (model) onChange({ models: [...new Set([...profile.models, model])].sort() });
    setAdded('');
  };
  return (
    <section className="settings-card" aria-label={`Models for ${profile.name}`}>
      <div className="row-between">
        <h3>{profile.name}</h3>
        <button className="button small" disabled={busy} onClick={() => void refresh()}>
          <RefreshCw size={13} className={busy ? 'spin' : undefined} />
          Refresh models
        </button>
      </div>
      {notice && (
        <p className="subtle" role="status">
          {notice}
        </p>
      )}
      <div className="chip-list">
        {profile.models.map((model) => (
          <span className="chip" key={model}>
            {model}
            <button
              aria-label={`Remove model ${model}`}
              onClick={() => onChange({ models: profile.models.filter((item) => item !== model) })}
            >
              <X size={12} />
            </button>
          </span>
        ))}
        {!profile.models.length && (
          <span className="subtle">No models yet. Refresh the list or add a model ID.</span>
        )}
      </div>
      <div className="row">
        <input
          aria-label={`Add model to ${profile.name}`}
          placeholder="Model ID, e.g. gpt-5 or claude-opus-5-5"
          value={added}
          onChange={(event) => setAdded(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              add();
            }
          }}
        />
        <button className="button" disabled={!added.trim()} onClick={add}>
          <Plus size={14} /> Add
        </button>
      </div>
      <div className="form-grid thirds">
        <label>
          Temperature
          <input
            type="number"
            step="0.1"
            min="0"
            max={profile.adapter === 'anthropic' ? 1 : 2}
            placeholder="Provider default"
            value={profile.temperature ?? ''}
            onChange={(event) => onChange({ temperature: optionalNumber(event.target.value) })}
          />
        </label>
        <label>
          Max output tokens
          <input
            type="number"
            min="1"
            value={profile.max_output_tokens}
            onChange={(event) => onChange({ max_output_tokens: Number(event.target.value) })}
          />
        </label>
        <label>
          Context window
          <input
            type="number"
            min="256"
            placeholder="Unknown"
            value={profile.context_window ?? ''}
            onChange={(event) => onChange({ context_window: optionalNumber(event.target.value) })}
          />
        </label>
      </div>
      <p className="subtle">
        Thinking counts toward the output limit. Leave temperature empty for models that reject it,
        such as current Claude models. A context window enables budget checks before a request is
        sent.
      </p>
    </section>
  );
}

/** Choose the default model and manage each provider's model list and request options. */
export function ModelsPage({
  draft,
  effortLevels,
  onChange,
}: {
  draft: Config;
  effortLevels: Record<string, string[]>;
  onChange: (config: Config) => void;
}) {
  const current = draft.provider;
  const providerId = current.profile_id || SIMULATED;
  const profile = draft.providers.find((item) => item.id === providerId);
  const setDefault = (patch: Partial<Config['provider']>) =>
    onChange({ ...draft, provider: { ...current, ...patch } });
  return (
    <>
      <p className="page-intro">
        The model list feeds the picker below the message box, where you choose a model for each
        run. The default applies to new conversations and to the CLI and API.
      </p>
      <section className="settings-card" aria-label="Default model">
        <h3>Default model</h3>
        <div className="form-grid thirds">
          <label>
            Provider
            <select
              aria-label="Default provider"
              value={providerId}
              onChange={(event) => {
                const next = draft.providers.find((item) => item.id === event.target.value);
                setDefault(
                  next
                    ? {
                        profile_id: next.id,
                        adapter: next.adapter,
                        model: next.models[0] || '',
                        reasoning_effort: null,
                      }
                    : {
                        profile_id: SIMULATED,
                        adapter: 'fake',
                        model: 'simulated',
                        reasoning_effort: null,
                      },
                );
              }}
            >
              {draft.providers.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
              <option value={SIMULATED}>Simulated model (offline)</option>
            </select>
          </label>
          <label>
            Model
            <select
              aria-label="Default model"
              value={current.model}
              disabled={!profile}
              onChange={(event) => setDefault({ model: event.target.value })}
            >
              {!profile && <option value="simulated">Simulated model</option>}
              {profile && !profile.models.includes(current.model) && (
                <option value={current.model}>{current.model || 'Choose a model'}</option>
              )}
              {profile?.models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </label>
          <label>
            Reasoning effort
            <select
              aria-label="Default reasoning effort"
              value={current.reasoning_effort || ''}
              disabled={!profile}
              onChange={(event) =>
                setDefault({
                  reasoning_effort: (event.target.value ||
                    null) as Config['provider']['reasoning_effort'],
                })
              }
            >
              <option value="">Default</option>
              {(profile ? effortLevels[profile.adapter] || [] : []).map((level) => (
                <option key={level} value={level}>
                  {effortLabels[level]}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>
      {draft.providers.map((item) => (
        <ProviderModels
          key={item.id}
          profile={item}
          onChange={(patch) =>
            onChange({
              ...draft,
              providers: draft.providers.map((other) =>
                other.id === item.id ? { ...other, ...patch } : other,
              ),
            })
          }
        />
      ))}
      {!draft.providers.length && (
        <p className="subtle">Add a provider on the Providers page to configure its models.</p>
      )}
    </>
  );
}
