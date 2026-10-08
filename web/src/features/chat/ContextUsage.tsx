import { useState } from 'react';
import type { components } from '../../api/generated';

type Context = components['schemas']['ContextView'];
type ContextRequest = components['schemas']['ContextRequest'];

// Columns stay readable down to a few pixels; older requests fold into the table view.
const CHART_REQUESTS = 60;

/** 1,284 / 12.9K / 1.2M: short enough for a stat tile, exact values stay in the table. */
function compact(value: number): string {
  if (value < 10_000) return value.toLocaleString();
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 100_000 ? 1 : 0)}K`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

function percent(part: number, whole: number): number {
  return Math.round((part / whole) * 100);
}

/** Totals over every model request of the conversation and its sub-agents. */
function SessionTotals({ context }: { context: Context }) {
  const usage = context.session_usage;
  const rate = usage.cache_hit_rate;
  return (
    <section className="context-usage" aria-label="Usage in this conversation">
      <div className="context-usage-head">
        <span>This conversation</span>
        <strong>
          {usage.requests} request{usage.requests === 1 ? '' : 's'}
        </strong>
      </div>
      <div className="usage-metrics">
        <div>
          <strong>
            {rate !== null && rate !== undefined ? `${Math.round(rate * 100)}%` : '—'}
          </strong>
          <span>Cache hit rate</span>
        </div>
        <div>
          <strong>{compact(usage.prompt_tokens)}</strong>
          <span>Prompt tokens</span>
        </div>
        <div>
          <strong>{compact(usage.output_tokens)}</strong>
          <span>Output tokens</span>
        </div>
      </div>
      <p className="subtle">
        {usage.cache_read_tokens !== null && usage.cache_read_tokens !== undefined
          ? `${usage.cache_read_tokens.toLocaleString()} prompt tokens served from cache`
          : 'Cache use not reported by this provider'}
        {usage.cache_write_tokens ? `, ${usage.cache_write_tokens.toLocaleString()} written` : ''}
        {usage.subagent_requests
          ? `. Includes ${usage.subagent_requests} sub-agent request${usage.subagent_requests === 1 ? '' : 's'}`
          : ''}
        {usage.reported_requests < usage.requests
          ? `. ${usage.requests - usage.reported_requests} reported no usage`
          : ''}
        .
      </p>
      <p className="subtle" data-testid="conversation-cost">
        Spent in this conversation: ${context.spent.toFixed(4)}
        {context.unpriced_runs > 0 &&
          ` · ${context.unpriced_runs} run${context.unpriced_runs === 1 ? '' : 's'} without a price or reported usage`}
        .
      </p>
    </section>
  );
}

/** One column per request: its prompt against the window, the cached part drawn separately. */
function RequestChart({ requests, scale }: { requests: ContextRequest[]; scale: number }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const shown = requests.slice(-CHART_REQUESTS);
  const offset = requests.length - shown.length;
  const caching = shown.some((item) => item.cache_read_tokens !== null);
  const focus = hovered ?? shown.length - 1;
  const current = shown[focus];
  const describe = (item: ContextRequest, index: number) => {
    const prompt = item.prompt_tokens;
    if (prompt === null || prompt === undefined) return `Request ${index}: no usage reported`;
    const window = item.context_window;
    return [
      `Request ${index}: ${prompt.toLocaleString()} prompt tokens`,
      window ? `${percent(prompt, window)}% of the window` : null,
      item.cache_read_tokens !== null && item.cache_read_tokens !== undefined
        ? `${item.cache_read_tokens.toLocaleString()} from cache`
        : null,
      item.model,
    ]
      .filter(Boolean)
      .join(' · ');
  };
  return (
    <figure className="request-chart">
      <figcaption>
        <span>Prompt size of each request</span>
        {caching && (
          <span className="chart-legend" aria-hidden="true">
            <span>
              <i data-series="cached" /> From cache
            </span>
            <span>
              <i data-series="uncached" /> Not cached
            </span>
          </span>
        )}
      </figcaption>
      <div className="request-plot">
        <div className="request-axis" aria-hidden="true">
          <span>{compact(scale)}</span>
          <span>0</span>
        </div>
        <div
          className="request-columns"
          role="img"
          aria-label={`${requests.length} requests; the latest used ${current ? describe(current, offset + focus + 1) : 'no tokens'}`}
          onMouseLeave={() => setHovered(null)}
        >
          {shown.map((item, index) => {
            const prompt = item.prompt_tokens ?? 0;
            const cached = Math.min(item.cache_read_tokens ?? 0, prompt);
            const height = (value: number) => `${Math.min(100, (value / scale) * 100)}%`;
            return (
              <div
                key={`${item.run_id}-${index}`}
                className="request-column"
                data-active={index === focus || undefined}
                onMouseEnter={() => setHovered(index)}
              >
                {/* The cached prefix sits on the baseline; the newly sent part stacks on it. */}
                {cached > 0 && <span data-series="cached" style={{ height: height(cached) }} />}
                <span data-series="uncached" style={{ height: height(prompt - cached) }} />
              </div>
            );
          })}
        </div>
      </div>
      <p className="request-readout" aria-live="polite">
        {current ? describe(current, offset + focus + 1) : ''}
        {offset > 0 && hovered === null ? ` · showing the last ${CHART_REQUESTS}` : ''}
      </p>
      <details className="request-table">
        <summary>Show requests as a table</summary>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Prompt</th>
              <th>Cached</th>
              <th>Output</th>
              <th>Window</th>
            </tr>
          </thead>
          <tbody>
            {requests.map((item, index) => (
              <tr key={`${item.run_id}-${index}`}>
                <td>{index + 1}</td>
                <td>{item.prompt_tokens?.toLocaleString() ?? '—'}</td>
                <td>{item.cache_read_tokens?.toLocaleString() ?? '—'}</td>
                <td>{item.output_tokens?.toLocaleString() ?? '—'}</td>
                <td>
                  {item.prompt_tokens != null && item.context_window
                    ? `${percent(item.prompt_tokens, item.context_window)}%`
                    : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** How full the context window is now, its peak, and every request's size over the conversation. */
function WindowUsage({ context }: { context: Context }) {
  const usage = context.live_usage;
  const window = context.context_window;
  const used = usage?.prompt_tokens;
  if (used === null || used === undefined)
    return (
      <p className="subtle">
        The last request ran{usage?.model ? ` on ${usage.model}` : ''}, but this provider reported
        no token usage.
      </p>
    );
  const fill = window ? Math.min(100, percent(used, window)) : null;
  const peak = context.session_usage.peak_prompt_tokens ?? used;
  const reported = context.requests.filter((item) => item.prompt_tokens !== null);
  const scale = Math.max(
    window ?? 0,
    ...reported.map((item) => item.prompt_tokens ?? 0),
    ...reported.map((item) => item.context_window ?? 0),
    1,
  );
  return (
    <section className="context-usage" aria-label="Context window">
      <div className="context-usage-head">
        <span>Context window now{usage?.model ? ` · ${usage.model}` : ''}</span>
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
      <p className="subtle">
        {fill !== null ? `${fill}% used by the latest request` : 'No context window configured'}
        {context.requests.length > 1
          ? ` · peak ${peak.toLocaleString()}${window ? ` (${percent(peak, window)}%)` : ''}`
          : ''}
        .
      </p>
      {reported.length > 1 && <RequestChart requests={context.requests} scale={scale} />}
    </section>
  );
}

/** Provider-reported usage: totals for the conversation, then its context window over time. */
export function ContextUsagePanel({ context }: { context: Context }) {
  if (!context.session_usage.requests)
    return (
      <p className="subtle">
        Usage from the provider appears after the first model request in this conversation.
      </p>
    );
  return (
    <>
      <SessionTotals context={context} />
      {context.live_usage && <WindowUsage context={context} />}
    </>
  );
}
