import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Layers, LoaderCircle, X } from 'lucide-react';
import {
  errorMessage,
  request,
  type Config,
  type ModelChoice,
  type Session,
} from '../../api/client';
import type { components } from '../../api/generated';
import { ContextUsagePanel } from './ContextUsage';

type Context = components['schemas']['ContextView'];

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
            <section className="context-usage" aria-label="Conversation history">
              <div className="context-usage-head">
                <span>History sent to the next run</span>
                <strong>~{context.effective_estimated_tokens.toLocaleString()} tokens</strong>
              </div>
              <div className="usage-metrics" data-columns="2">
                <div>
                  <strong>{context.raw_messages}</strong>
                  <span>Messages in this conversation</span>
                </div>
                <div>
                  <strong>{context.effective_messages}</strong>
                  <span>{active ? 'Sent now, with the summary' : 'Sent now'}</span>
                </div>
              </div>
              <p className="subtle">
                Byte estimates, not a tokenizer; instructions and tools come on top. Model context:{' '}
                {context.context_window?.toLocaleString() || 'not configured'}; reserved output:{' '}
                {context.reserved_output_tokens.toLocaleString()}.
              </p>
            </section>
            <section className="context-usage" aria-label="Compaction">
              <div className="context-usage-head">
                <span>Compaction</span>
              </div>
              <p className="subtle">
                Summarizes older turns with the model selected below the message box. Every turn
                stays in this conversation; new runs send the summary and the recent turns instead,
                until you restore full context.
              </p>
              <div className="compact-controls">
                <label className="inline-field">
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
                <button
                  className="button primary"
                  disabled={
                    busy || !!session.active_run_id || !!context.compacting_id || session.archived
                  }
                  onClick={() =>
                    void action('compact', 'POST', {
                      expected_revision: context.revision,
                      config_revision: config.revision,
                      keep_recent_turns: keep,
                      model,
                    })
                  }
                >
                  Compact older turns
                </button>
              </div>
              {context.compacting_id && (
                <div className="compact-status">
                  <span>
                    <LoaderCircle size={14} className="spin" /> Summarizing older turns…
                  </span>
                  <button
                    className="button danger"
                    disabled={busy}
                    onClick={() =>
                      void action(`compactions/${context.compacting_id}/cancel`, 'POST')
                    }
                  >
                    Stop compaction
                  </button>
                </div>
              )}
              {active && (
                <div className="compact-status">
                  <span>
                    Summary of {active.covered_messages} messages in use since{' '}
                    {new Date(active.created_at).toLocaleString()}
                  </span>
                  <button
                    className="button"
                    disabled={busy || !!session.active_run_id || !!context.compacting_id}
                    onClick={() =>
                      void action('context', 'PUT', { expected_revision: context.revision })
                    }
                  >
                    Restore full context
                  </button>
                </div>
              )}
              <p className="subtle" data-testid="auto-compact-status">
                {config.auto_compact?.enabled
                  ? `Automatic compaction is on: after a run, history over ~${config.auto_compact.threshold_tokens.toLocaleString()} tokens is summarized, keeping ${config.auto_compact.keep_recent_turns} recent turn${config.auto_compact.keep_recent_turns === 1 ? '' : 's'}.`
                  : 'Automatic compaction is off; turn it on in Settings › Context.'}
              </p>
            </section>
            {active && (
              <section className="context-section">
                <h3>Active summary</h3>
                <p className="subtle">
                  {active.covered_messages} original messages · revision {active.source_revision} ·{' '}
                  {active.model_calls} model calls
                </p>
                <pre className="context-summary">{active.summary}</pre>
              </section>
            )}
            {context.compactions.length > 0 && (
              <section className="context-section">
                <h3>Compaction history</h3>
                {[...context.compactions].reverse().map((item) => (
                  <details key={item.id} className="context-file">
                    <summary>
                      <span>
                        {item.trigger === 'auto' ? 'Automatic · ' : ''}
                        {item.status} · {item.covered_messages} messages
                      </span>
                      <span className="subtle">{new Date(item.created_at).toLocaleString()}</span>
                    </summary>
                    <div className="context-file-body">
                      {item.error && <p className="error-text">{item.error}</p>}
                      <p className="subtle">Source: {item.source_hash.slice(0, 16)}…</p>
                      <pre className="context-summary">
                        {item.summary || 'No summary activated.'}
                      </pre>
                      <a
                        href={`/api/v1/sessions/${session.id}/compactions/${item.id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Inspect recorded requests
                      </a>
                    </div>
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
