import type { Config } from '../../api/client';

const defaults = {
  enabled: false,
  window_percent: 90 as number | null,
  threshold_tokens: null as number | null,
  keep_recent_turns: 2,
};

/** Opt-in automatic compaction: the same recorded, reversible summary as the Context panel. */
export function ContextPage({
  draft,
  onChange,
}: {
  draft: Config;
  onChange: (config: Config) => void;
}) {
  const value = { ...defaults, ...draft.auto_compact };
  const set = (changes: Partial<typeof value>) =>
    onChange({ ...draft, auto_compact: { ...value, ...changes } });
  // The window condition needs each model's context window; name the providers without one.
  const unknown = draft.providers.filter((provider) => !provider.context_window);
  return (
    <>
      <p className="page-intro">
        Compaction summarizes older turns so new runs send less history. The Context panel in each
        conversation compacts on request; here you can let it happen automatically after a run.
      </p>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(event) => set({ enabled: event.target.checked })}
        />
        Compact automatically
      </label>
      <fieldset className="compact-triggers" disabled={!value.enabled}>
        <legend>Start when either is reached</legend>
        <div className="compact-trigger">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={value.window_percent !== null}
              onChange={(event) => set({ window_percent: event.target.checked ? 90 : null })}
            />
            The latest request fills
          </label>
          <input
            type="number"
            aria-label="Share of the context window (%)"
            min="50"
            max="99"
            disabled={value.window_percent === null}
            value={value.window_percent ?? 90}
            onChange={(event) => set({ window_percent: Number(event.target.value) })}
          />
          <span>% of the model&apos;s context window</span>
        </div>
        <div className="compact-trigger">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={value.threshold_tokens !== null}
              onChange={(event) => set({ threshold_tokens: event.target.checked ? 100_000 : null })}
            />
            The history the next run sends exceeds
          </label>
          <input
            type="number"
            aria-label="Estimated history size (tokens)"
            min="1000"
            step="1000"
            disabled={value.threshold_tokens === null}
            value={value.threshold_tokens ?? 100_000}
            onChange={(event) => set({ threshold_tokens: Number(event.target.value) })}
          />
          <span>tokens (byte estimate)</span>
        </div>
        <label className="inline-field">
          Recent turns kept verbatim
          <select
            value={value.keep_recent_turns}
            onChange={(event) => set({ keep_recent_turns: Number(event.target.value) })}
          >
            {Array.from({ length: 10 }, (_, index) => (
              <option key={index} value={index + 1}>
                {index + 1}
              </option>
            ))}
          </select>
        </label>
      </fieldset>
      {value.window_percent !== null && unknown.length > 0 && (
        <p className="notice">
          {unknown.map((provider) => provider.name).join(', ')}{' '}
          {unknown.length === 1 ? 'has' : 'have'} no context window, so the window condition never
          applies to {unknown.length === 1 ? 'its' : 'their'} models. Set it in Settings › Models.
        </p>
      )}
      <p className="subtle">
        The check runs after each successful run, using that run&apos;s model: the provider&apos;s
        reported prompt size, or an estimate when it reports none. Summaries cost model calls.
        Original messages stay in the conversation, the summary is marked automatic in Context, and
        Restore full context undoes it. A single run that outgrows the window is not compacted while
        it runs.
      </p>
    </>
  );
}
