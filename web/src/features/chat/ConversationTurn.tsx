import { useState, type ReactNode } from 'react';
import {
  Ban,
  BookOpen,
  Bot,
  Check,
  CircleAlert,
  Eye,
  FilePen,
  FileText,
  GitBranch,
  Globe,
  ListChecks,
  LoaderCircle,
  Plug,
  Search,
  ShieldAlert,
  ShieldCheck,
  SquareTerminal,
  User,
  Wrench,
  X,
} from 'lucide-react';
import Markdown, { type Components } from 'react-markdown';
import { Json } from '../../shared/Json';
import { Diff } from '../../shared/Diff';
import { TaskPlan } from './TaskPlan';
import { RunFiles } from './RunFiles';
import { StatusBadge } from '../../shared/StatusBadge';
import { modelLabel } from '../composer/choices';
import { terminal, type Run, type ResumeRequest } from '../../api/client';

type Message = Run['messages'][number];
type ToolCall = Message['tool_calls'][number];
type StepState = 'done' | 'failed' | 'running' | 'waiting' | 'missing';

// Output images need explicit user navigation, never automatic remote requests.
const markdownComponents: Components = {
  img: ({ alt }) => <span className="subtle">[Image: {alt || 'external content'}]</span>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
};

/** Render every persisted turn: the task, the agent's text and paired tool steps, and controls. */
/** Name what "approve for this conversation" will cover: one shell program, or the tool. */
function grantLabel(approval: Record<string, unknown>) {
  const name = String(approval.name);
  const args = (approval.args || {}) as Record<string, unknown>;
  if ((name === 'bash' || name === 'powershell') && typeof args.command === 'string') {
    const program = args.command.trim().split(/\s+/)[0];
    if (program) return `Allow ${program} commands in this conversation`;
  }
  return `Allow ${name} in this conversation`;
}

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
  onApprove: (decision: 'approve' | 'approve_conversation' | 'reject') => void;
  onResume: (resolutions: NonNullable<ResumeRequest['resolutions']>) => void;
}) {
  const results = new Map(
    run.messages
      .filter((message) => message.role === 'tool' && message.tool_call_id)
      .map((message) => [message.tool_call_id!, message]),
  );
  const paired = new Set(
    run.messages.flatMap((message) => message.tool_calls.map((call) => call.id)),
  );
  const pending = run.messages.flatMap((message) =>
    message.tool_calls.filter((call) => !results.has(call.id)),
  );
  const stepState = (call: ToolCall): StepState => {
    if (results.has(call.id))
      return outcome(call.name, results.get(call.id)!.content).failed ? 'failed' : 'done';
    if (run.status === 'waiting_approval' && run.approval?.tool_call_id === call.id)
      return 'waiting';
    if (!terminal(run.status) && run.status !== 'interrupted' && call === pending[0])
      return 'running';
    return 'missing';
  };
  const working = ['queued', 'running'].includes(run.status) && !run.partial_output;
  return (
    <section
      className="conversation-turn"
      id={`turn-${run.id}`}
      data-selected={selected}
      aria-label={`Turn ${index + 1}`}
    >
      <div className="turn-heading">
        <span className="turn-number">#{index + 1}</span>
        <time dateTime={run.created_at} title={new Date(run.created_at).toLocaleString()}>
          {new Date(run.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </time>
        <StatusBadge status={run.status} />
        <span className="turn-preset" title="Prompt preset and model">
          {run.preset_name}
          {(run.model || run.provider === 'fake') &&
            ` · ${
              run.provider === 'fake'
                ? 'Simulated model'
                : modelLabel({
                    provider_id: run.provider_id || '',
                    model: run.model || '',
                    reasoning_effort: run.reasoning_effort as never,
                  })
            }`}
        </span>
        {(run.access === 'read-only' || run.access === 'ask') && (
          <span className="badge">{run.access === 'ask' ? 'ask before changes' : 'read only'}</span>
        )}
        {run.source_run_id && (
          <span className="badge" title={`Source run: ${run.source_run_id}`}>
            Copied history
          </span>
        )}
        <div className="turn-actions">
          <button
            className="icon-button"
            aria-label={`Inspect turn ${index + 1}`}
            title="Inspect requests and tool evidence"
            disabled={busy}
            onClick={onInspect}
          >
            <Eye size={15} />
          </button>
          {canFork && (
            <button
              className="button small quiet"
              aria-label={`Fork after turn ${index + 1}`}
              title="Copy this conversation up to here into a new one"
              disabled={busy}
              onClick={onFork}
            >
              <GitBranch size={13} /> Fork here
            </button>
          )}
        </div>
      </div>
      <div className="task-message">
        <span className="message-avatar user-avatar" aria-hidden>
          <User size={14} />
        </span>
        <div className="message-body">
          <span className="message-caption">You</span>
          {run.command ? (
            <>
              <p>
                <code className="command-invocation">
                  /{run.command.name}
                  {run.command.arguments ? ` ${run.command.arguments}` : ''}
                </code>
              </p>
              <details className="command-expansion">
                <summary>Sent as</summary>
                <p>{run.input}</p>
              </details>
            </>
          ) : (
            <p>{run.input}</p>
          )}
        </div>
      </div>
      <div className="agent-activity">
        <span className="message-avatar agent-avatar" aria-hidden>
          <Bot size={15} />
        </span>
        <div className="message-body">
          <span className="message-caption">Agent</span>
          {run.plan && <TaskPlan plan={run.plan} />}
          {run.messages.map((message, messageIndex) => {
            if (message.role === 'tool')
              return paired.has(message.tool_call_id || '') ? null : (
                <OrphanResult key={messageIndex} message={message} />
              );
            return (
              <div className="chat-message" key={messageIndex}>
                {message.content && (
                  <div className="markdown">
                    <Markdown skipHtml components={markdownComponents}>
                      {message.content}
                    </Markdown>
                  </div>
                )}
                {message.tool_calls.map((call) => (
                  <ToolStep
                    key={call.id}
                    call={call}
                    result={results.get(call.id)?.content}
                    state={stepState(call)}
                    live={stepState(call) === 'running' ? run.active_tool : null}
                  />
                ))}
              </div>
            );
          })}
          {run.partial_output && (
            <div className="chat-message streaming">
              <div className="markdown">
                <Markdown skipHtml components={markdownComponents}>
                  {run.partial_output}
                </Markdown>
              </div>
            </div>
          )}
          {working && (
            <div className="agent-progress" role="status">
              <span className="activity-dot" />
              {run.status === 'queued' ? 'Waiting for a free worker…' : 'Working…'}
            </div>
          )}
          {run.error && (
            <p role="alert" className="notice warning">
              <CircleAlert size={15} />
              <span>{run.error}</span>
            </p>
          )}
          {run.status === 'waiting_approval' && run.approval && (
            <div className="approval-card">
              <div className="row">
                <ShieldCheck size={18} />
                <h3>Permission to run a tool</h3>
              </div>
              <p>
                <strong>{String(run.approval.name)}</strong> is waiting for your decision.
              </p>
              <Json value={run.approval.args} />
              <div className="row">
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() => onApprove('approve')}
                >
                  <Check size={14} />
                  Approve once
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => onApprove('approve_conversation')}
                >
                  {grantLabel(run.approval)}
                </button>
                <button className="button" disabled={busy} onClick={() => onApprove('reject')}>
                  Reject
                </button>
              </div>
              <p className="subtle">
                Approve once covers only the arguments above. The conversation choice also runs
                later matching calls in this conversation without asking; revoke it in Tools.
              </p>
            </div>
          )}
          {run.status === 'interrupted' && <Recovery run={run} busy={busy} onResume={onResume} />}
          {terminal(run.status) && run.changed_files.length > 0 && (
            <RunFiles run={run} busy={busy} />
          )}
        </div>
      </div>
    </section>
  );
}

/** Pick the argument that identifies what a tool call acts on, for its one-line summary. */
function target(call: ToolCall): string {
  const args = call.args as Record<string, unknown>;
  const text = (value: unknown) => (typeof value === 'string' ? value : '');
  switch (call.name) {
    case 'bash':
    case 'powershell':
    case 'start_process':
      return text(args.command).split('\n')[0];
    case 'search_text':
      return `“${text(args.query)}”${args.path ? ` in ${text(args.path)}` : ''}`;
    case 'search_files':
      return text(args.pattern) || '**/*';
    case 'apply_patch':
      return [...text(args.patch).matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm)]
        .map((match) => match[1].trim())
        .join(', ');
    case 'fetch_url':
      return text(args.url);
    case 'load_skill':
    case 'save_skill':
      return text(args.name);
    case 'update_plan':
      return Array.isArray(args.plan) ? `${args.plan.length} steps` : '';
    case 'poll_process':
    case 'send_process_input':
    case 'stop_process':
      return text(args.process_id).slice(0, 10);
    case 'echo':
      return text(args.text);
    default:
      return text(args.path) || text(args.name) || '';
  }
}

/** Choose a tool icon by capability, so a step's effect is recognizable at a glance. */
function toolIcon(name: string): ReactNode {
  if (name.startsWith('mcp_')) return <Plug size={14} />;
  if (
    [
      'bash',
      'powershell',
      'start_process',
      'poll_process',
      'send_process_input',
      'stop_process',
    ].includes(name)
  )
    return <SquareTerminal size={14} />;
  if (['write_text', 'edit_text', 'apply_patch', 'save_memory', 'save_skill'].includes(name))
    return <FilePen size={14} />;
  if (['search_files', 'search_text'].includes(name)) return <Search size={14} />;
  if (['load_skill', 'read_memory', 'read_instructions'].includes(name))
    return <BookOpen size={14} />;
  if (name === 'fetch_url') return <Globe size={14} />;
  if (name === 'update_plan') return <ListChecks size={14} />;
  if (['read_text', 'list_files'].includes(name)) return <FileText size={14} />;
  return <Wrench size={14} />;
}

/** Classify a recorded tool result: explicit errors and failing commands are failures. */
function outcome(name: string, content: string): { failed: boolean; label: string } {
  let value: unknown = null;
  try {
    value = JSON.parse(content);
  } catch {
    return { failed: false, label: '' };
  }
  if (!value || typeof value !== 'object') return { failed: false, label: '' };
  const result = value as Record<string, unknown>;
  const error = result.error as Record<string, unknown> | undefined;
  if (error && typeof error === 'object' && typeof error.code === 'string')
    return { failed: true, label: error.code.replaceAll('_', ' ') };
  if (['bash', 'powershell'].includes(name) && 'exit_code' in result) {
    if (result.timed_out) return { failed: true, label: 'timed out' };
    return {
      failed: result.exit_code !== 0,
      label: result.exit_code === null ? 'stopped' : `exit ${String(result.exit_code)}`,
    };
  }
  return { failed: false, label: '' };
}

/** One tool call with its paired result, collapsed to a single readable line by default. */
function ToolStep({
  call,
  result,
  state,
  live,
}: {
  call: ToolCall;
  result?: string;
  state: StepState;
  live: Run['active_tool'] | null;
}) {
  const summary = target(call);
  const label = result ? outcome(call.name, result).label : '';
  const statusIcon = {
    done: <Check size={14} />,
    failed: <X size={14} />,
    running: <LoaderCircle size={14} className="spin" />,
    waiting: <ShieldAlert size={14} />,
    missing: <Ban size={14} />,
  }[state];
  const statusText = {
    done: label || 'done',
    failed: label || 'failed',
    running: 'running',
    waiting: 'needs approval',
    missing: 'no result',
  }[state];
  return (
    <details className="tool-step" data-state={state} open={state === 'running' ? true : undefined}>
      <summary>
        <span className="tool-step-icon">{toolIcon(call.name)}</span>
        <span className="tool-step-name">{call.name}</span>
        {summary && (
          <span className="tool-step-target" title={summary}>
            {summary}
          </span>
        )}
        <span className="tool-step-status">
          {statusIcon}
          {statusText}
        </span>
      </summary>
      <div className="tool-step-body">
        <div className="section-label">Arguments</div>
        <Json value={call.args} />
        {result !== undefined ? (
          <>
            <div className="section-label">Result</div>
            <ToolOutput content={result} />
          </>
        ) : live && (live.stdout || live.stderr) ? (
          <>
            <div className="section-label">Live output</div>
            {!!live.stdout && <pre className="command-output">{String(live.stdout)}</pre>}
            {!!live.stderr && <pre className="command-output stderr">{String(live.stderr)}</pre>}
          </>
        ) : (
          <p className="subtle">
            {state === 'running'
              ? 'Waiting for output…'
              : state === 'waiting'
                ? 'Approve or reject this call below.'
                : 'This call has no recorded result.'}
          </p>
        )}
      </div>
    </details>
  );
}

/** A result whose call is not in this Run's messages; shown rather than silently dropped. */
function OrphanResult({ message }: { message: Message }) {
  return (
    <details className="tool-step tool-result" data-state="done">
      <summary>
        <span className="tool-step-icon">
          <Wrench size={14} />
        </span>
        <span className="tool-step-name">Tool result</span>
        <span className="tool-step-target">{message.tool_call_id}</span>
      </summary>
      <div className="tool-step-body">
        <ToolOutput content={message.content} />
      </div>
    </details>
  );
}

/** Color unified diff lines so additions and removals are distinguishable. */
/** Display actual tool output in its natural form instead of an escaped JSON string. */
function ToolOutput({ content }: { content: string }) {
  let value: unknown;
  try {
    value = JSON.parse(content);
  } catch {
    // Plain-text and MCP results use the same fallback as an unrecognized JSON shape.
    return <pre className="command-output">{content}</pre>;
  }
  if (!value || typeof value !== 'object') return <pre className="command-output">{content}</pre>;
  const result = value as Record<string, unknown>;
  const error = result.error as Record<string, unknown> | undefined;
  if (error && typeof error === 'object' && typeof error.message === 'string')
    return (
      <div className="tool-error">
        <strong>{String(error.code || 'error').replaceAll('_', ' ')}</strong>
        <span>{error.message}</span>
        {error.details != null && <Json value={error.details} />}
      </div>
    );
  const files = result.files;
  if (
    Array.isArray(files) &&
    files.length > 0 &&
    files.every((file: unknown) => !!file && typeof (file as { diff?: unknown }).diff === 'string')
  )
    return (
      <div className="patch-results">
        {(files as Record<string, unknown>[]).map((file, index) => (
          <div key={index}>
            <div className="file-label">
              {String(file.path)} <span className="badge">{String(file.operation)}</span>
            </div>
            <Diff text={String(file.diff)} />
            {file.diff_truncated === true && (
              <p className="subtle">Diff truncated. Read the current file for complete content.</p>
            )}
          </div>
        ))}
      </div>
    );
  if (typeof result.stdout === 'string' && typeof result.stderr === 'string')
    return (
      <>
        <div className="command-meta">
          <span className="badge">exit {String(result.exit_code ?? '—')}</span>
          {result.timed_out === true && <span className="badge warning">timed out</span>}
          {result.truncated === true && <span className="badge">output shortened</span>}
          {result.effects_may_be_partial === true && (
            <span className="subtle">Edits may be partial; inspect files before repeating.</span>
          )}
        </div>
        {result.stdout && <pre className="command-output">{result.stdout}</pre>}
        {result.stderr && <pre className="command-output stderr">{result.stderr}</pre>}
        {!result.stdout && !result.stderr && <p className="subtle">No output.</p>}
      </>
    );
  if (typeof result.content === 'string')
    return (
      <>
        {typeof result.start_line === 'number' && (
          <p className="subtle">
            {String(result.path)} · lines {String(result.start_line)}–{String(result.end_line)} of{' '}
            {String(result.total_lines)}
            {result.next_line ? ` · continues at line ${String(result.next_line)}` : ''}
          </p>
        )}
        <pre className="command-output">{result.content}</pre>
      </>
    );
  if (Array.isArray(result.matches))
    return (
      <pre className="command-output">
        {(result.matches as Record<string, unknown>[])
          .map((match) => `${String(match.path)}:${String(match.line)}  ${String(match.text)}`)
          .join('\n') || 'No matches.'}
        {result.truncated === true && '\n… more results omitted; narrow the search.'}
      </pre>
    );
  if (Array.isArray(result.files) && result.files.every((file) => typeof file === 'string'))
    return (
      <pre className="command-output">
        {(result.files as string[]).join('\n') || 'No files.'}
        {result.truncated === true && '\n… more files omitted.'}
      </pre>
    );
  if (Array.isArray(result.entries))
    return (
      <pre className="command-output">
        {(result.entries as { name: string; directory: boolean }[])
          .map((entry) => entry.name + (entry.directory ? '/' : ''))
          .join('\n') || 'Empty directory.'}
      </pre>
    );
  return <Json value={value} />;
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
      <div className="row">
        <CircleAlert size={18} />
        <h3>Review interrupted execution</h3>
      </div>
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
