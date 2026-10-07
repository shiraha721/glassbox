import type { Config } from '../../api/client';

const defaults = { enabled: false, threshold_tokens: 100_000, keep_recent_turns: 2 };

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
  return (
    <>
      <p className="page-intro">
        Compaction summarizes older turns so new runs send less history. The Context panel in each
        conversation compacts on request; here you can let it happen automatically.
      </p>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(event) => set({ enabled: event.target.checked })}
        />
        Compact automatically when a conversation&apos;s history grows large
      </label>
      <div className="form-grid">
        <label>
          History size that triggers it (estimated tokens)
          <input
            type="number"
            min="1000"
            step="1000"
            disabled={!value.enabled}
            value={value.threshold_tokens}
            onChange={(event) => set({ threshold_tokens: Number(event.target.value) })}
          />
        </label>
        <label>
          Recent turns kept verbatim
          <select
            disabled={!value.enabled}
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
      </div>
      <p className="subtle">
        After a run succeeds, if the history the next run would send is larger than this, older
        turns are summarized with that run&apos;s model. This costs model calls. Original messages
        stay in the conversation, the summary is listed in Context as automatic, and Restore full
        context undoes it.
      </p>
    </>
  );
}
