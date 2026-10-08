import { Layers } from 'lucide-react';
import type { Compaction } from './compactions';

/** A divider in the conversation where older turns were summarized, however it was started.
 *
 * The active summary says what the model now sees instead of the turns above; replaced,
 * restored and failed summaries stay visible, quieter, so the history of context is honest. */
export function CompactionMarker({
  item,
  active,
  onOpen,
}: {
  item: Compaction;
  active: boolean;
  onOpen: () => void;
}) {
  const how = item.trigger === 'auto' ? 'Automatically' : 'Manually';
  const when = new Date(item.created_at).toLocaleString();
  const state =
    item.status === 'running'
      ? 'running'
      : item.status !== 'succeeded'
        ? 'failed'
        : active
          ? 'active'
          : 'inactive';
  const title = {
    running: `${how} summarizing ${item.covered_messages} earlier messages…`,
    active: `${how} summarized ${item.covered_messages} earlier messages`,
    inactive: `Earlier summary of ${item.covered_messages} messages, no longer in use`,
    failed: `${item.trigger === 'auto' ? 'Automatic' : 'Manual'} summary ${item.status}`,
  }[state];
  return (
    <section className="compaction-marker" data-state={state} aria-label="Context compaction">
      <div className="compaction-line">
        <Layers size={14} />
        <strong>{title}</strong>
        <span className="subtle">{when}</span>
        <button className="text-button" onClick={onOpen}>
          Context
        </button>
      </div>
      {state === 'active' && (
        <p className="subtle">
          New runs send this summary and the turns below instead of the full turns above. The
          original messages are kept; Restore full context in Context switches back.
        </p>
      )}
      {state === 'failed' && item.error && <p className="subtle">{item.error}</p>}
      {item.summary && (
        <details>
          <summary>Summary</summary>
          <pre className="context-summary">{item.summary}</pre>
        </details>
      )}
    </section>
  );
}
