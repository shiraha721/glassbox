import { useState } from 'react';
import { Bot, Check, Eye, GitBranch, ShieldCheck, Terminal, User } from 'lucide-react';
import Markdown, { type Components } from 'react-markdown';
import { Json } from '../../shared/Json';
import { TaskPlan } from './TaskPlan';
import type { Run, ResumeRequest } from '../../api/client';

// Output images need explicit user navigation, never automatic remote requests.
const markdownComponents: Components = {
  img: ({ alt }) => <span className="subtle">[Image: {alt || 'external content'}]</span>,
};

/** Render every persisted turn, with tools and continuation controls next to their evidence. */
export function RunTurn({
  run,
  index,
  busy,
  selected,
  canFork,
  onInspect,
  onFork,
  onApprove,
  onResume,
}: {
  run: Run;
  index: number;
  busy: boolean;
  selected: boolean;
  canFork: boolean;
  onInspect: () => void;
  onFork: () => void;
  onApprove: (decision: 'approve' | 'reject') => void;
  onResume: (resolutions: NonNullable<ResumeRequest['resolutions']>) => void;
}) {
  return (
    <section
      className="conversation-turn"
      id={`turn-${run.id}`}
      data-selected={selected}
      aria-label={`Turn ${index + 1}`}
    >
      <div className="turn-heading">
        <span className="turn-number">{String(index + 1).padStart(2, '0')}</span>
        <time dateTime={run.created_at} title={new Date(run.created_at).toLocaleString()}>
          {new Date(run.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </time>
        <span className={`status status-${run.status}`}>{run.status.replaceAll('_', ' ')}</span>
        {run.source_run_id && (
          <span className="badge" title={`Source run: ${run.source_run_id}`}>
            Copied history
          </span>
        )}
        <div className="turn-actions">
          <button
            className="icon-button"
            aria-label={`Inspect turn ${index + 1}`}
            title="Inspect turn"
            disabled={busy}
            onClick={onInspect}
          >
            <Eye size={14} />
          </button>
          {canFork && (
            <button
              className="button small quiet"
              aria-label={`Fork after turn ${index + 1}`}
              disabled={busy}
              onClick={onFork}
            >
              <GitBranch size={13} /> Fork here
            </button>
          )}
        </div>
      </div>
      <div className="task-message">
        <span className="message-avatar user-avatar">
          <User size={15} />
        </span>
        <div className="message-body">
          <span className="message-caption">You</span>
          <p>{run.input}</p>
        </div>
      </div>
      {run.plan && <TaskPlan plan={run.plan} />}
      {run.messages.map((message, messageIndex) => (
        <article key={messageIndex} className={`chat-message chat-${message.role}`}>
          {message.role === 'tool' ? (
            <details className="tool-result">
              <summary>
                <Terminal size={14} /> Tool result{' '}
                <span className="subtle">{message.tool_call_id?.slice(-10)}</span>
              </summary>
              <ToolOutput content={message.content} />
            </details>
          ) : (
            <>
              <span className="message-avatar agent-avatar">
                <Bot size={16} />
              </span>
              <div className="message-body">
                <span className="message-caption">
                  {message.role === 'assistant' ? 'Agent' : message.role}
                </span>
                {message.content && (
                  <div className="markdown">
                    <Markdown skipHtml components={markdownComponents}>
                      {message.content}
                    </Markdown>
                  </div>
                )}
                {message.tool_calls.map((call) => (
                  <details className="tool-request" key={call.id}>
                    <summary>
                      <Terminal size={14} />
                      <strong>{call.name}</strong>
                      <span className="subtle">Tool call</span>
                    </summary>
                    <pre>{JSON.stringify(call.args, null, 2)}</pre>
                  </details>
                ))}
              </div>
            </>
          )}
        </article>
      ))}
      {run.partial_output && (
        <article className="chat-message streaming">
          <span className="message-avatar agent-avatar">
            <Bot size={16} />
          </span>
          <div className="message-body">
            <span className="message-caption">
              Agent <span className="streaming-label">Thinking…</span>
            </span>
            <div className="markdown">
              <Markdown skipHtml components={markdownComponents}>
                {run.partial_output}
              </Markdown>
            </div>
          </div>
        </article>
      )}
      {run.active_tool && (
        <article className="live-tool-output">
          <div className="row">
            <Terminal size={15} />
            <strong>{String(run.active_tool.name)}</strong>
            <span className="activity-dot" />
            Running
          </div>
          {!!(run.active_tool.stdout || run.active_tool.stderr) && (
            <pre>
              {String(run.active_tool.stdout || '')}
              {run.active_tool.stderr ? `\n[stderr]\n${String(run.active_tool.stderr)}` : ''}
            </pre>
          )}
          {!!run.active_tool.truncated && <span className="subtle">Output truncated</span>}
        </article>
      )}
      {['queued', 'running'].includes(run.status) && !run.partial_output && (
        <div className="agent-progress">
          <span className="activity-dot" />
          Agent is working…
        </div>
      )}
      {run.error && (
        <p role="alert" className="notice warning">
          {run.error}
        </p>
      )}
      {run.status === 'waiting_approval' && run.approval && (
        <div className="approval-card">
          <div className="row">
            <ShieldCheck size={19} />
            <h3>Permission to run a tool</h3>
          </div>
          <p>
            <strong>{String(run.approval.name)}</strong> is waiting for your decision.
          </p>
          <Json value={run.approval.args} />
          <div className="row">
            <button className="button primary" disabled={busy} onClick={() => onApprove('approve')}>
              <Check size={14} />
              Approve once
            </button>
            <button className="button" disabled={busy} onClick={() => onApprove('reject')}>
              Reject
            </button>
          </div>
          <p className="subtle">This decision applies only to the arguments shown above.</p>
        </div>
      )}
      {run.status === 'interrupted' && <Recovery run={run} busy={busy} onResume={onResume} />}
    </section>
  );
}

/** Display actual command output without forcing users to read an escaped JSON string. */
function ToolOutput({ content }: { content: string }) {
  let value: unknown;
  try {
    value = JSON.parse(content);
  } catch {
    // Plain-text and MCP results use the same fallback as an unrecognized JSON shape.
    value = null;
  }
  if (value && typeof value === 'object') {
    const result = value as Record<string, unknown>;
    if (
      Array.isArray(result.files) &&
      result.files.length > 0 &&
      result.files.every(
        (file: unknown) =>
          !!file &&
          typeof file === 'object' &&
          typeof (file as Record<string, unknown>).diff === 'string',
      )
    )
      return (
        <div className="patch-results">
          {result.files.map((file: Record<string, unknown>, index: number) => (
            <div key={index}>
              <strong>
                {String(file.path)} · {String(file.operation)}
              </strong>
              <pre className="patch-diff">{String(file.diff)}</pre>
              {file.diff_truncated === true && (
                <p className="subtle">
                  Diff truncated. Read the current file for complete content.
                </p>
              )}
            </div>
          ))}
        </div>
      );
    if (typeof result.stdout === 'string' && typeof result.stderr === 'string')
      return (
        <>
          <div className="subtle">
            Exit {String(result.exit_code)}
            {result.timed_out ? ' · timed out; edits may be partial' : ''}
            {result.truncated ? ' · output truncated' : ''}
          </div>
          {result.stdout && <pre>{result.stdout}</pre>}
          {result.stderr && <pre>{result.stderr}</pre>}
        </>
      );
    if (typeof result.content === 'string')
      return (
        <>
          {typeof result.start_line === 'number' && (
            <p className="subtle">
              {String(result.path)} · lines {String(result.start_line)}–{String(result.end_line)} of{' '}
              {String(result.total_lines)}
              {result.next_line ? ` · more from line ${String(result.next_line)}` : ''}
            </p>
          )}
          <pre>{result.content}</pre>
        </>
      );
  }
  return <pre>{content}</pre>;
}

/** Require a per-call recovery choice; uncertain external effects are never silently repeated. */
function Recovery({
  run,
  busy,
  onResume,
}: {
  run: Run;
  busy: boolean;
  onResume: (resolutions: NonNullable<ResumeRequest['resolutions']>) => void;
}) {
  const [resolutions, setResolutions] = useState<NonNullable<ResumeRequest['resolutions']>>({});
  return (
    <div className="approval-card recovery-card">
      <h3>Review interrupted execution</h3>
      <p>
        Some calls may have completed outside the studio. Retrying can repeat an external effect or
        model charge.
      </p>
      {run.unknown_calls.map((call) => (
        <div key={String(call.id)} className="recovery-call">
          <strong>{String(call.kind)} call</strong>
          <Json value={call.data} />
          <label>
            Recovery decision
            <select
              value={resolutions[String(call.id)]?.action || ''}
              onChange={(event) =>
                setResolutions({
                  ...resolutions,
                  [String(call.id)]: {
                    action: event.target.value as 'retry' | 'record',
                    result: '',
                  },
                })
              }
            >
              <option value="" disabled>
                Choose explicitly…
              </option>
              <option value="retry">Retry this call</option>
              {call.kind === 'tool' && <option value="record">Record a verified result</option>}
            </select>
          </label>
          {resolutions[String(call.id)]?.action === 'record' && (
            <label>
              Verified tool result
              <textarea
                value={resolutions[String(call.id)].result}
                onChange={(event) =>
                  setResolutions({
                    ...resolutions,
                    [String(call.id)]: { action: 'record', result: event.target.value },
                  })
                }
              />
            </label>
          )}
        </div>
      ))}
      <button
        className="button primary"
        disabled={busy || run.unknown_calls.some((call) => !resolutions[String(call.id)])}
        onClick={() => onResume(resolutions)}
      >
        Resume from checkpoint
      </button>
    </div>
  );
}
