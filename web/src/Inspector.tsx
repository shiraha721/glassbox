import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, X } from 'lucide-react';
import { request, type PromptInspection, type Run, type Trace } from './api/client';

/** Render structured evidence as text; provider and tool payloads never become executable HTML. */
export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

/** Inspect preview provenance or the saved evidence for every model and tool invocation. */
export function Inspector({
  preview,
  previewUpdating,
  previewError,
  run,
  tab,
  onTab,
  onClose,
}: {
  preview: PromptInspection | null;
  previewUpdating: boolean;
  previewError: string;
  run?: Run;
  tab: 'preview' | 'trace';
  onTab: (tab: 'preview' | 'trace') => void;
  onClose: () => void;
}) {
  const [raw, setRaw] = useState(false);
  const trace = useQuery({
    queryKey: ['trace', run?.id, raw],
    enabled: !!run,
    queryFn: () => request<Trace>(`/runs/${run!.id}/trace?raw=${raw}`),
  });
  return (
    <aside className="inspector panel" aria-label="Request inspector">
      <div className="panel-heading">
        <h2>Inspector</h2>
        <button className="icon-button" aria-label="Close inspector" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className="tabs" role="tablist" aria-label="Inspector view">
        <button role="tab" aria-selected={tab === 'preview'} onClick={() => onTab('preview')}>
          Prompt preview
        </button>
        <button role="tab" aria-selected={tab === 'trace'} onClick={() => onTab('trace')}>
          Run trace {trace.data && <span className="count">{trace.data.calls.length}</span>}
        </button>
      </div>
      <div className="inspector-content">
        {tab === 'preview' && (
          <div className="preview-state" role="status" aria-live="polite">
            {previewError ? (
              <p role="alert" className="error-text">
                {previewError} {preview && 'Showing the last valid preview.'}
              </p>
            ) : previewUpdating ? (
              <span>Updating preview… {preview && 'Showing the previous draft.'}</span>
            ) : preview ? (
              <span>Live preview · updates as you edit · Run captures current input</span>
            ) : null}
          </div>
        )}
        {tab === 'preview' &&
          (preview ? (
            <>
              <div className="metric-grid">
                <div>
                  <strong>{preview.compiled.messages.length}</strong>
                  <span>messages</span>
                </div>
                <div>
                  <strong>~{preview.compiled.token_estimate.toLocaleString()}</strong>
                  <span>estimated tokens</span>
                </div>
                <div>
                  <strong>{preview.tools.length}</strong>
                  <span>tools</span>
                </div>
              </div>
              <p className="subtle">
                Context: {preview.compiled.budget_status}. Estimate uses a byte heuristic, not the
                provider tokenizer.
              </p>
              <div className="hash">
                <span>{preview.input_hash.slice(0, 20)}</span>
                <span className="badge">
                  {previewUpdating || previewError ? 'Previous draft' : 'Live draft'}
                </span>
              </div>
              <h3>Final message sequence</h3>
              {preview.compiled.messages.map((message, index) => (
                <details
                  className="message-preview"
                  key={index}
                  open={message.source === 'current_input'}
                >
                  <summary>
                    <span className="block-number">{String(index + 1).padStart(2, '0')}</span>
                    <span className={`role role-${message.role}`}>{message.role}</span>
                    <span className="source" title={message.source}>
                      {message.source}
                    </span>
                  </summary>
                  <pre>
                    {message.content ||
                      (message.tool_calls.length
                        ? '(Tool call)'
                        : message.source === 'current_input'
                          ? '(No task input)'
                          : '(Empty message)')}
                  </pre>
                  {message.tool_calls.length > 0 && <Json value={message.tool_calls} />}
                </details>
              ))}
              <details className="evidence">
                <summary>Block activation</summary>
                <Json value={preview.compiled.activation_report} />
              </details>
              <details className="evidence">
                <summary>Prepared request body</summary>
                <Json value={preview.prepared_request} />
              </details>
              <details className="evidence">
                <summary>Available tool schemas</summary>
                <Json value={preview.tools} />
              </details>
            </>
          ) : (
            <div className="empty-inspector">Enter a message and select Preview.</div>
          ))}
        {tab === 'trace' &&
          (run ? (
            <>
              {run.source_run_id && (
                <p className="notice">
                  Copied history · requests, results, and usage come from the source run. No calls
                  were executed by this copy.
                </p>
              )}
              <div className="row-between">
                <span className="badge">{raw ? 'Original local view' : 'Redacted view'}</span>
                <a className="text-button" href={`/api/v1/runs/${run.id}/export`} download>
                  <Download size={13} />
                  Export JSON
                </a>
              </div>
              <label className="checkbox-label trace-original">
                <input
                  type="checkbox"
                  checked={raw}
                  onChange={(event) => setRaw(event.target.checked)}
                />
                Show original trace
              </label>
              <p className="subtle">
                Run {run.id.slice(0, 8)} · {new Date(run.created_at).toLocaleString()}
              </p>
              <p className="workspace-path">Run workspace: {run.workspace_root}</p>
              <div className="metric-grid">
                <div>
                  <strong>{String(run.usage.input_tokens ?? '—')}</strong>
                  <span>input tokens</span>
                </div>
                <div>
                  <strong>{String(run.usage.output_tokens ?? '—')}</strong>
                  <span>output tokens</span>
                </div>
                <div>
                  <strong>
                    {typeof run.usage.active_seconds === 'number'
                      ? run.usage.active_seconds.toFixed(1) + 's'
                      : '—'}
                  </strong>
                  <span>active time</span>
                </div>
              </div>
              {trace.isError && (
                <p className="error-text" role="alert">
                  {trace.error.message}
                </p>
              )}
              {trace.isPending && <p className="subtle">Loading evidence…</p>}
              {trace.data?.calls.map((call, index) => (
                <details key={call.id} className="trace-call" open={index === 0}>
                  <summary>
                    <span className="call-number">{String(index + 1).padStart(2, '0')}</span>
                    <strong>
                      {call.kind === 'model' ? 'Model request' : String(call.data.name)}
                    </strong>
                    <span className={`status status-${call.status}`}>
                      {call.status.replaceAll('_', ' ')}
                    </span>
                  </summary>
                  {call.kind === 'model' ? (
                    <>
                      {call.data.simulated === true && (
                        <p className="notice">Simulated model · no network request</p>
                      )}
                      <details className="evidence">
                        <summary>Canonical messages & sources</summary>
                        <Json
                          value={{
                            messages: call.data.canonical_messages,
                            sources: call.data.source_map,
                          }}
                        />
                      </details>
                      <details className="evidence">
                        <summary>Prepared request</summary>
                        <Json value={call.data.prepared_request} />
                      </details>
                      <details className="evidence">
                        <summary>Actual outbound body</summary>
                        <Json value={call.data.wire_request_body} />
                      </details>
                      <details className="evidence">
                        <summary>Usage & transformations</summary>
                        <Json
                          value={{
                            usage: call.data.usage,
                            transformations: call.data.transformations,
                            metadata: call.data.response_metadata,
                            latency_ms: call.data.latency_ms,
                            attempts: call.data.attempts,
                          }}
                        />
                      </details>
                    </>
                  ) : (
                    <Json
                      value={{
                        arguments: call.data.args,
                        decision: call.data.decision,
                        tool_call_id: call.data.tool_call_id,
                      }}
                    />
                  )}
                  <details className="evidence">
                    <summary>Result</summary>
                    <Json value={call.result} />
                  </details>
                  {call.data.attempt_history != null && (
                    <details className="evidence">
                      <summary>Previous attempts</summary>
                      <Json value={call.data.attempt_history} />
                    </details>
                  )}
                </details>
              ))}
            </>
          ) : (
            <div className="empty-inspector">No run selected.</div>
          ))}
      </div>
    </aside>
  );
}
