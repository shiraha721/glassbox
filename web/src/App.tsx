import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  Code2,
  Eye,
  FolderOpen,
  MessageSquare,
  PanelLeft,
  Play,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';
import Markdown, { type Components } from 'react-markdown';
import {
  errorMessage,
  request,
  terminal,
  type Bootstrap,
  type Preset,
  type Preview,
  type ResumeRequest,
  type Run,
  type Session,
} from './api/client';
import { useRunEvents } from './api/events';
import { Composer } from './Composer';
import { Inspector, Json } from './Inspector';
import { Settings } from './Settings';
import { WorkspacePanel } from './WorkspacePanel';

// Untrusted output must not trigger automatic requests to remote image URLs.
const markdownComponents: Components = {
  img: ({ alt }) => <span className="subtle">[Image: {alt || 'external content'}]</span>,
};

/** Load the local workspace directly, without accounts or an unlock screen. */
export default function App() {
  const query = useQuery({
    queryKey: ['bootstrap'],
    queryFn: () => request<Bootstrap>('/bootstrap'),
  });
  if (query.isPending) return <div className="loading-screen">Loading workspace…</div>;
  if (query.isError && !query.data)
    return (
      <div className="loading-screen">
        <p role="alert">{query.error.message}</p>
        <button className="button" onClick={() => void query.refetch()}>
          Retry
        </button>
      </div>
    );
  return <Workspace data={query.data!} />;
}

/** Coordinate saved server state with a small local draft and a single expiring preview. */
function Workspace({ data }: { data: Bootstrap }) {
  const cache = useQueryClient();
  const [preset, setPreset] = useState(data.presets[0]);
  const [dirty, setDirty] = useState(false);
  const [sessionId, setSessionId] = useState(() => localStorage.getItem('studio.session') || '');
  const [selectedRun, setSelectedRun] = useState('');
  const [input, setInput] = useState('');
  const [variables, setVariables] = useState('{}');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewRevision, setPreviewRevision] = useState(0);
  const [tab, setTab] = useState<'preview' | 'trace'>(() =>
    localStorage.getItem('studio.session') ? 'trace' : 'preview',
  );
  const [settings, setSettings] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(
    () => window.matchMedia('(min-width: 761px)').matches,
  );
  const [panel, setPanel] = useState<'prompt' | 'inspector' | 'workspace' | null>(null);
  const [skills, setSkills] = useState<string[]>([]);
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const session = data.sessions.find((item) => item.id === sessionId) || data.sessions[0];
  const runQuery = useQuery({
    queryKey: ['runs', session?.id],
    enabled: !!session,
    queryFn: () => request<Run[]>(`/sessions/${session!.id}/runs`),
    refetchInterval: (query) =>
      query.state.data?.some((run) => !terminal(run.status)) ? 2000 : false,
  });
  const runs = runQuery.data || [];
  const run = runs.find((item) => item.id === selectedRun) || runs.at(-1);
  const active = runs.find((item) => !terminal(item.status));
  useRunEvents(active?.id, !!active);
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ['bootstrap'] }),
      cache.invalidateQueries({ queryKey: ['runs'] }),
      cache.invalidateQueries({ queryKey: ['trace'] }),
    ]);
  };
  const perform = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  const chooseSession = (id: string) => {
    localStorage.setItem('studio.session', id);
    setSessionId(id);
    if (window.matchMedia('(max-width: 760px)').matches) setSidebarOpen(false);
    setSelectedRun('');
    setPanel(null);
    setPreview(null);
    setInput('');
    setError('');
  };
  const newSession = async (keepDraft = false) => {
    const created = await request<Session>('/sessions', 'POST', {});
    await refresh();
    chooseSession(created.id);
    if (keepDraft) setInput(input);
    return created;
  };
  const savePreset = async (): Promise<Preset> => {
    if (!dirty) return preset;
    const saved = await request<Preset>(`/presets/${preset.id}`, 'PUT', preset);
    setPreset(saved);
    setDirty(false);
    setPreview(null);
    await refresh();
    return saved;
  };
  const inputRequest = async () => {
    const parsed: unknown = JSON.parse(variables);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
      throw new Error('Variables must be a JSON object.');
    const saved = await savePreset();
    const current = session || (await newSession(true));
    return {
      session_id: current.id,
      expected_session_revision: current.revision,
      preset_id: saved.id,
      preset_revision: saved.revision,
      config_revision: data.config.revision,
      input,
      variables: parsed,
      skills,
    };
  };
  const buildPreview = async () => {
    const body = await inputRequest();
    const compiled = await request<Preview>('/prompts/preview', 'POST', body);
    setPreviewRevision(body.expected_session_revision);
    setPreview(compiled);
    setTab('preview');
    setPanel('inspector');
  };
  const start = async () => {
    const body = preview
      ? {
          preview_id: preview.id,
          input_hash: preview.input_hash,
          expected_session_revision: previewRevision,
        }
      : await inputRequest();
    const fingerprint = JSON.stringify(body);
    // An explicit retry of unchanged input keeps its identity even if the response was lost.
    if (submission.current?.fingerprint !== fingerprint)
      submission.current = { fingerprint, key: crypto.randomUUID() };
    const created = await request<Run>('/runs', 'POST', body, submission.current.key);
    submission.current = null;
    setSelectedRun(created.id);
    setInput('');
    setPreview(null);
    setTab('trace');
    setPanel(null);
    await refresh();
  };
  const changeInput = (text: string) => {
    setInput(text);
    setPreview(null);
  };
  return (
    <div className="studio">
      <aside
        className="sidebar"
        id="conversation-sidebar"
        aria-label="Conversations"
        hidden={!sidebarOpen}
      >
        <div className="sidebar-heading">
          <Code2 size={18} />
          <strong>Agent Studio</strong>
          <button
            className="icon-button"
            aria-label="Close sidebar"
            onClick={() => setSidebarOpen(false)}
          >
            <PanelLeft size={16} />
          </button>
        </div>
        <button
          className="nav-button new-conversation"
          disabled={busy}
          onClick={() =>
            void perform(async () => {
              await newSession();
            })
          }
        >
          <Plus size={16} /> New conversation
        </button>
        <div className="sidebar-label">Conversations</div>
        <nav className="session-list" aria-label="Conversation history">
          {data.sessions.map((item) => (
            <button
              key={item.id}
              className="session-item"
              aria-current={session?.id === item.id ? 'page' : undefined}
              disabled={busy}
              title={item.title}
              onClick={() => chooseSession(item.id)}
            >
              <MessageSquare size={14} />
              <span>{item.title}</span>
              {item.active_run_id && <span className="activity-dot" aria-label="Active run" />}
            </button>
          ))}
          {!data.sessions.length && <p className="subtle sidebar-empty">No conversations</p>}
        </nav>
        <div className="sidebar-footer">
          <button
            className="nav-button"
            disabled={busy}
            onClick={() => setPanel(panel === 'workspace' ? null : 'workspace')}
          >
            <FolderOpen size={16} /> Workspace
          </button>
          <button className="nav-button" disabled={busy} onClick={() => setSettings(true)}>
            <SlidersHorizontal size={16} /> Settings
          </button>
        </div>
      </aside>
      {sidebarOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="Dismiss sidebar"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <div className="workspace">
        <header className="topbar">
          <button
            className="icon-button"
            aria-label="Toggle sidebar"
            aria-expanded={sidebarOpen}
            aria-controls="conversation-sidebar"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            <PanelLeft size={18} />
          </button>
          <h1 title={session?.title}>{session?.title || 'New conversation'}</h1>
          {run && (
            <span className={`status status-${run.status}`} data-testid="run-status">
              {run.status.replaceAll('_', ' ')}
            </span>
          )}
          <div className="topbar-actions">
            <button
              className="button quiet"
              disabled={busy}
              aria-pressed={panel === 'prompt'}
              onClick={() => setPanel(panel === 'prompt' ? null : 'prompt')}
            >
              <SlidersHorizontal size={15} /> Prompt{dirty ? ' *' : ''}
            </button>
            <button
              className="button quiet"
              disabled={busy}
              aria-pressed={panel === 'inspector'}
              onClick={() => setPanel(panel === 'inspector' ? null : 'inspector')}
            >
              <Eye size={15} /> Inspector
            </button>
            {session && (
              <button
                className="icon-button"
                aria-label="Delete conversation"
                title="Delete conversation"
                disabled={!!session.active_run_id || busy}
                onClick={() => {
                  if (
                    window.confirm(
                      'Delete this conversation, its runs, and all local execution records?',
                    )
                  )
                    void perform(async () => {
                      await request(`/sessions/${session.id}`, 'DELETE');
                      chooseSession('');
                      await refresh();
                    });
                }}
              >
                <Trash2 size={15} />
              </button>
            )}
          </div>
        </header>
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}>
              <X size={14} />
            </button>
          </div>
        )}
        <main className="workbench">
          <section className="conversation" aria-label="Conversation workspace">
            <div className="conversation-scroll" aria-live="polite">
              {runs.length > 0 && (
                <div className="run-switcher">
                  <label htmlFor="run-selector">Run history</label>
                  <select
                    id="run-selector"
                    value={run?.id || ''}
                    onChange={(event) => {
                      setSelectedRun(event.target.value);
                      setTab('trace');
                    }}
                  >
                    {runs.map((item, index) => (
                      <option key={item.id} value={item.id}>
                        #{index + 1} · {item.input.slice(0, 45)} · {item.status}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {runQuery.isError && (
                <p role="alert" className="error-text">
                  {runQuery.error.message}
                </p>
              )}
              {run ? (
                <>
                  <div className="task-message">
                    <span className="message-caption">You</span>
                    <p>{run.input}</p>
                  </div>
                  {run.messages.map((message, index) => (
                    <article key={index} className={`chat-message chat-${message.role}`}>
                      {message.role === 'tool' ? (
                        <details className="tool-result">
                          <summary>
                            <Terminal size={14} />
                            Tool result
                            <span className="subtle">{message.tool_call_id?.slice(-10)}</span>
                          </summary>
                          <ToolOutput content={message.content} />
                        </details>
                      ) : (
                        <>
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
                            <div className="tool-request" key={call.id}>
                              <Terminal size={14} />
                              <strong>{call.name}</strong>
                              <code>{JSON.stringify(call.args)}</code>
                            </div>
                          ))}
                        </>
                      )}
                    </article>
                  ))}
                  {run.partial_output && (
                    <article className="chat-message streaming">
                      <span className="message-caption">Agent · streaming</span>
                      <div className="markdown">
                        <Markdown skipHtml components={markdownComponents}>
                          {run.partial_output}
                        </Markdown>
                      </div>
                    </article>
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
                        <button
                          className="button primary"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              await request(
                                `/runs/${run.id}/approvals/${String(run.approval!.id)}`,
                                'POST',
                                { decision: 'approve' },
                              );
                              await refresh();
                            })
                          }
                        >
                          <Check size={14} />
                          Approve once
                        </button>
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() =>
                            void perform(async () => {
                              await request(
                                `/runs/${run.id}/approvals/${String(run.approval!.id)}`,
                                'POST',
                                { decision: 'reject' },
                              );
                              await refresh();
                            })
                          }
                        >
                          Reject
                        </button>
                      </div>
                      <p className="subtle">
                        This decision applies only to the arguments shown above.
                      </p>
                    </div>
                  )}
                  {run.status === 'interrupted' && (
                    <Recovery
                      key={run.id}
                      run={run}
                      busy={busy}
                      onResume={(resolutions) =>
                        void perform(async () => {
                          await request(`/runs/${run.id}/resume`, 'POST', { resolutions });
                          await refresh();
                        })
                      }
                    />
                  )}
                </>
              ) : (
                <div className="conversation-empty">No messages yet.</div>
              )}
            </div>
            <div className="task-composer">
              <label className="sr-only" htmlFor="task-input">
                Task
              </label>
              <textarea
                id="task-input"
                value={input}
                onChange={(event) => changeInput(event.target.value)}
                placeholder="Message the agent…"
                rows={3}
                disabled={!!active || busy}
                onKeyDown={(event) => {
                  if (
                    event.key === 'Enter' &&
                    (event.ctrlKey || event.metaKey) &&
                    input.trim() &&
                    !active &&
                    !busy
                  ) {
                    event.preventDefault();
                    void perform(start);
                  }
                }}
              />
              {skills.length > 0 && (
                <div className="selected-skills">Skills: {skills.join(', ')}</div>
              )}
              <details className="task-variables">
                <summary>
                  Input variables <span className="subtle">JSON</span>
                </summary>
                <textarea
                  aria-label="Input variables JSON"
                  disabled={!!active || busy}
                  className="code-input"
                  rows={3}
                  value={variables}
                  onChange={(event) => {
                    setVariables(event.target.value);
                    setPreview(null);
                  }}
                />
              </details>
              <div className="composer-actions">
                <button
                  className="model-button"
                  disabled={busy}
                  onClick={() => setSettings(true)}
                  title="Model settings"
                >
                  {data.config.provider.adapter === 'fake'
                    ? 'Simulated model'
                    : data.config.provider.model || 'Configure model'}
                </button>
                <div className="row">
                  {active ? (
                    <button
                      className="button danger"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          await request(`/runs/${active.id}/cancel`, 'POST');
                          await refresh();
                        })
                      }
                    >
                      <Square size={13} />
                      Stop run
                    </button>
                  ) : (
                    <>
                      <button
                        className="button"
                        disabled={busy || !input.trim()}
                        onClick={() => void perform(buildPreview)}
                      >
                        <Eye size={14} />
                        {dirty ? 'Save & preview' : 'Preview'}
                      </button>
                      <button
                        className="button primary"
                        disabled={busy || !input.trim()}
                        onClick={() => void perform(start)}
                      >
                        <Play size={13} />
                        Run
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          </section>
          {/* Keep editors mounted so toggling a panel does not discard unapplied rule drafts. */}
          <div className="utility-pane" hidden={panel !== 'prompt'}>
            <Composer
              preset={preset}
              presets={data.presets}
              dirty={dirty}
              busy={busy}
              onClose={() => setPanel(null)}
              onChange={(value) => {
                setPreset(value);
                setDirty(true);
                setPreview(null);
              }}
              onSelect={(value) => {
                setPreset(value);
                setDirty(false);
                setPreview(null);
              }}
              onSave={() =>
                void perform(async () => {
                  await savePreset();
                })
              }
            />
          </div>
          <div className="utility-pane" hidden={panel !== 'inspector'}>
            <Inspector
              key={run?.id || 'empty'}
              preview={preview}
              run={run}
              tab={tab}
              onTab={setTab}
              onClose={() => setPanel(null)}
            />
          </div>
          {panel === 'workspace' && (
            <div className="utility-pane">
              <WorkspacePanel
                selected={skills}
                onSelect={(value) => {
                  setSkills(value);
                  setPreview(null);
                }}
                onClose={() => setPanel(null)}
                onChanged={() => setPreview(null)}
              />
            </div>
          )}
        </main>
      </div>
      {settings && (
        <Settings
          data={data}
          onClose={() => setSettings(false)}
          onSaved={async () => {
            setPreview(null);
            await refresh();
          }}
        />
      )}
    </div>
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
    if (typeof result.content === 'string') return <pre>{result.content}</pre>;
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
