import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Layers, X } from 'lucide-react';
import {
  errorMessage,
  request,
  type Config,
  type ModelChoice,
  type Session,
} from '../../api/client';
import type { components } from '../../api/generated';

type Context = components['schemas']['ContextView'];

/** Show the provider's accounting for the latest request: how much of the window it filled and
 * how much of its prompt the model's cache served, with provider-truthful numbers only. */
function ContextUsagePanel({ context }: { context: Context }) {
  const usage = context.live_usage;
  if (!usage)
    return (
      <p className="subtle">
        Live usage from the provider appears after the first model request in this conversation.
      </p>
    );
  if (usage.prompt_tokens === null)
    return (
      <p className="subtle">
        The last request ran{usage.model ? ` on ${usage.model}` : ''}, but this provider reported no
        token usage.
      </p>
    );
  const window = context.context_window;
  const used = usage.prompt_tokens;
  const fill = window ? Math.min(100, Math.round((used / window) * 100)) : null;
  // Cache reads are a subset of the prompt; the rate is meaningful only when both are reported.
  const cached = usage.cache_read_tokens;
  const rate = cached !== null && used > 0 ? Math.round((cached / used) * 100) : null;
  return (
    <section className="context-usage">
      <div className="context-usage-head">
        <span>Last request{usage.model ? ` · ${usage.model}` : ''}</span>
        <strong>
          {used.toLocaleString()}
          {window ? ` / ${window.toLocaleString()}` : ''} tokens
        </strong>
      </div>
      {fill !== null && (
        <div
          className="usage-bar"
          role="progressbar"
          aria-valuenow={fill}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Context window used"
        >
          <span style={{ width: `${fill}%` }} data-high={fill >= 85 || undefined} />
        </div>
      )}
      <div className="usage-metrics">
        <div>
          <strong>{window ? `${fill}%` : '—'}</strong>
          <span>Window used</span>
        </div>
        <div>
          <strong>{rate !== null ? `${rate}%` : '—'}</strong>
          <span>Cache hit rate</span>
        </div>
        <div>
          <strong>{usage.output_tokens?.toLocaleString() ?? '—'}</strong>
          <span>Output tokens</span>
        </div>
      </div>
      <p className="subtle">
        {cached !== null ? `${cached.toLocaleString()} prompt tokens served from cache` : 'Cache'}
        {usage.cache_write_tokens ? `, ${usage.cache_write_tokens.toLocaleString()} written` : ''}
        {cached === null ? ' not reported by this provider' : ''}.
      </p>
    </section>
  );
}

/** Inspect history accounting and explicitly summarize or restore older complete turns. */
export function ContextPanel({
  session,
  config,
  model,
  onClose,
  onChanged,
}: {
  session: Session;
  config: Config;
  model: ModelChoice | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [keep, setKeep] = useState(2);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const query = useQuery({
    queryKey: ['context', session.id],
    queryFn: () => request<Context>(`/sessions/${session.id}/context`),
    refetchInterval: (state) => (state.state.data?.compacting_id ? 1000 : false),
  });
  const context = query.data;
  const active = context?.compactions.find((item) => item.id === context.active_compaction_id);

  /** Sequence an explicit revision-checked change, then refresh the saved context and session. */
  async function action(path: string, method: string, body?: unknown) {
    setBusy(true);
    setError('');
    try {
      await request(`/sessions/${session.id}/${path}`, method, body);
      await query.refetch();
      await onChanged();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="panel workspace-panel" aria-label="Conversation context">
      <div className="panel-heading">
        <Layers size={16} />
        <h2>Context</h2>
        <button className="icon-button" aria-label="Close context" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className="workspace-content">
        {query.isPending && <p className="subtle">Loading context…</p>}
        {(error || query.isError) && (
          <p className="error-banner" role="alert">
            {error || errorMessage(query.error)}
          </p>
        )}
        {context && (
          <>
            <ContextUsagePanel context={context} />
            <div className="context-accounting">
              <div>
                <strong>{context.raw_messages}</strong>
                <span>Original messages</span>
                <small>~{context.raw_estimated_tokens.toLocaleString()} tokens</small>
              </div>
              <div>
                <strong>{context.effective_messages}</strong>
                <span>Current history</span>
                <small>~{context.effective_estimated_tokens.toLocaleString()} tokens</small>
              </div>
            </div>
            <p className="subtle" data-testid="conversation-cost">
              Spent in this conversation: ${context.spent.toFixed(4)}
              {context.unpriced_runs > 0 &&
                ` · ${context.unpriced_runs} run${context.unpriced_runs === 1 ? '' : 's'} without a price or reported usage`}
              .
            </p>
            <p className="subtle">
              History estimates use bytes, not a tokenizer. Prompt inspection includes instructions
              and tools. Model context:{' '}
              {context.context_window?.toLocaleString() || 'not configured'}; reserved output:{' '}
              {context.reserved_output_tokens.toLocaleString()}.
            </p>
            <label>
              Keep recent turns
              <select
                aria-label="Keep recent turns"
                value={keep}
                onChange={(event) => setKeep(Number(event.target.value))}
              >
                {Array.from({ length: 10 }, (_, index) => (
                  <option key={index} value={index + 1}>
                    {index + 1}
                  </option>
                ))}
              </select>
            </label>
            <p className="subtle">
              Compaction calls the model selected below the message box. Original messages stay in
              history; new runs use a summary and the recent turns. You can restore full context.
            </p>
            <div className="row">
              <button
                className="button primary"
                disabled={
                  busy || !!session.active_run_id || !!context.compacting_id || session.archived
                }
                onClick={() =>
                  void action('compact', 'POST', {
                    expected_revision: session.revision,
                    config_revision: config.revision,
                    keep_recent_turns: keep,
                    model,
                  })
                }
              >
                Compact older turns
              </button>
              {active && (
                <button
                  className="button"
                  disabled={busy || !!session.active_run_id || !!context.compacting_id}
                  onClick={() =>
                    void action('context', 'PUT', { expected_revision: session.revision })
                  }
                >
                  Restore full context
                </button>
              )}
            </div>
            {context.compacting_id && (
              <div className="row">
                <span className="subtle">Compacting…</span>
                <button
                  className="button danger"
                  disabled={busy}
                  onClick={() => void action(`compactions/${context.compacting_id}/cancel`, 'POST')}
                >
                  Stop compaction
                </button>
              </div>
            )}
            {active && (
              <section>
                <h3>Active summary</h3>
                <p className="subtle">
                  {active.covered_messages} original messages · revision {active.source_revision} ·{' '}
                  {active.model_calls} model calls
                </p>
                <pre className="context-summary">{active.summary}</pre>
              </section>
            )}
            {context.compactions.length > 0 && (
              <section>
                <h3>Compaction history</h3>
                {[...context.compactions].reverse().map((item) => (
                  <details key={item.id} className="context-file">
                    <summary>
                      {item.status} · {item.covered_messages} messages ·{' '}
                      {new Date(item.created_at).toLocaleString()}
                    </summary>
                    {item.error && <p role="alert">{item.error}</p>}
                    <p className="subtle">Source: {item.source_hash}</p>
                    <pre className="context-summary">{item.summary || 'No summary activated.'}</pre>
                    <a
                      href={`/api/v1/sessions/${session.id}/compactions/${item.id}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Inspect recorded request
                    </a>
                  </details>
                ))}
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
