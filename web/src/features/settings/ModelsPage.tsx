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

type Price = NonNullable<ProviderProfile['prices']>[string];
const priceFields = [
  ['input', 'Input'],
  ['output', 'Output'],
  ['cache_read', 'Cache read'],
  ['cache_write', 'Cache write'],
] as const;

/** Per-model prices in US dollars per million tokens; an all-empty row removes the price. */
function PriceTable({
  profile,
  onChange,
}: {
  profile: ProviderProfile;
  onChange: (patch: Partial<ProviderProfile>) => void;
}) {
  const prices = profile.prices ?? {};
  const set = (model: string, field: keyof Price, value: string) => {
    const next: Price = { ...prices[model], [field]: optionalNumber(value) };
    const rest = Object.fromEntries(Object.entries(prices).filter(([key]) => key !== model));
    const empty = priceFields.every(([key]) => next[key] === null || next[key] === undefined);
    onChange({ prices: empty ? rest : { ...rest, [model]: next } });
  };
  return (
    <details className="price-table">
      <summary>Prices · US$ per million tokens</summary>
      {profile.models.length ? (
        <table>
          <thead>
            <tr>
              <th>Model</th>
              {priceFields.map(([, label]) => (
                <th key={label}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {profile.models.map((model) => (
              <tr key={model}>
                <th scope="row">{model}</th>
                {priceFields.map(([field, label]) => (
                  <td key={field}>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      aria-label={`${label} price for ${model}`}
                      value={prices[model]?.[field] ?? ''}
                      onChange={(event) => set(model, field, event.target.value)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="subtle">Add models first.</p>
      )}
      <p className="subtle">
        Runs are priced when both input and output prices are set. Empty cache prices count as the
        input price. A Run keeps the prices it started with.
      </p>
    </details>
  );
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
          placeholder="Model ID"
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
      <PriceTable profile={profile} onChange={onChange} />
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
        Thinking counts toward the output limit. Leave temperature empty to send none, so each model
        uses its own default; many reasoning models accept only 1, and current Claude models reject
        any. A context window enables budget checks before a request is sent, the window bar in
        Context, and compaction when a request fills it.
      </p>
      {profile.adapter !== 'openai' ? (
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={profile.show_reasoning}
            onChange={(event) => onChange({ show_reasoning: event.target.checked })}
          />
          Show the model&apos;s thinking
        </label>
      ) : null}
      <p className="subtle">
        {profile.adapter === 'anthropic'
          ? 'Asks for summarized thinking, shown under Thinking in each turn. Needs a model with adaptive thinking (Claude 4.6 or later); older models reject the request.'
          : profile.adapter === 'responses'
            ? 'Asks for reasoning summaries, shown under Thinking in each turn. OpenAI may require a verified organization for them and otherwise rejects the request.'
            : 'Chat Completions endpoints do not return the model’s reasoning through this adapter; use the Responses API or Anthropic to see it.'}
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
