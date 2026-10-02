import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRight,
  Check,
  ChevronRight,
  Code2,
  Eye,
  FileText,
  LockKeyhole,
  MessageSquare,
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
  authenticate,
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

// Untrusted output must not trigger automatic requests to remote image URLs.
const markdownComponents: Components = {
  img: ({ alt }) => <span className="subtle">[Image: {alt || 'external content'}]</span>,
};

/** Restore the local browser session before exposing saved workspace content. */
export default function App() {
  const cache = useQueryClient();
  const auth = useQuery({
    queryKey: ['auth'],
    queryFn: () => authenticate().catch(() => false),
    staleTime: Infinity,
  });
  useEffect(() => {
    const lock = () => {
      cache.setQueryData(['auth'], false);
      cache.removeQueries({ predicate: (query) => query.queryKey[0] !== 'auth' });
    };
    window.addEventListener('studio:locked', lock);
    return () => window.removeEventListener('studio:locked', lock);
  }, [cache]);
  if (auth.isPending)
    return (
      <div className="loading-screen">
        <span className="brand-icon">
          <Code2 />
        </span>
        Opening local workspace…
      </div>
    );
  if (!auth.data) return <Unlock onUnlock={() => cache.setQueryData(['auth'], true)} />;
  return <WorkspaceLoader />;
}

/** Exchange the token once; the controlled password value is discarded on unmount. */
function Unlock({ onUnlock }: { onUnlock: () => void }) {
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <main className="unlock-page">
      <div className="unlock-brand">
        <span className="brand-icon">
          <Code2 size={22} />
        </span>
        SECURITY AGENT STUDIO<span className="badge">LOCAL</span>
      </div>
      <div className="unlock-layout">
        <section className="unlock-story">
          <div className="eyebrow">THE WORKSPACE FOR INSPECTABLE AGENTS</div>
          <h1>
            Compose with intent.
            <br />
            <span>Inspect every step.</span>
          </h1>
          <p>
            Build the prompt. Set the boundaries. Follow the evidence from the first instruction to
            the final tool result.
          </p>
          <div className="unlock-features">
            <span>
              <Check size={15} />
              Composable messages
            </span>
            <span>
              <Check size={15} />
              Explicit tool approval
            </span>
            <span>
              <Check size={15} />
              Local execution records
            </span>
          </div>
          <div className="schematic">
            <span>prompt</span>
            <ChevronRight size={16} />
            <span>model</span>
            <ChevronRight size={16} />
            <span>tool</span>
            <ChevronRight size={16} />
            <span className="active">evidence</span>
          </div>
        </section>
        <form
          className="unlock-card"
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            void authenticate(token)
              .then(onUnlock)
              .catch((caught: unknown) => setError(errorMessage(caught)))
              .finally(() => setBusy(false));
          }}
        >
          <div className="lock-symbol">
            <LockKeyhole size={25} />
          </div>
          <h2>Open your workspace</h2>
          <p>Use the access token created by your local server.</p>
          <label htmlFor="access-token">Local access token</label>
          <input
            id="access-token"
            type="password"
            autoComplete="off"
            required
            autoFocus
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="Paste your token"
          />
          <div className="token-help">
            <span>Find it in the repository:</span>
            <code>Get-Content var/access-token</code>
            <small>Or use your configured STUDIO_ACCESS_TOKEN.</small>
          </div>
          {error && (
            <p role="alert" className="error-text">
              {error}
            </p>
          )}
          <button className="button primary wide" disabled={busy}>
            {busy ? 'Opening…' : 'Open workspace'}
            <ArrowRight size={16} />
          </button>
          <p className="subtle">
            <ShieldCheck size={13} />
            Your token stays on this device.
          </p>
        </form>
      </div>
      <footer>
        Security Agent Studio <span>v0.1 · Single-agent workspace</span>
      </footer>
    </main>
  );
}

/** Fetch initial data before mounting editable local state, so refetches cannot erase drafts. */
function WorkspaceLoader() {
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
    setSelectedRun('');
    setPreview(null);
    setInput('');
    setError('');
  };
  const newSession = async () => {
    const created = await request<Session>('/sessions', 'POST', {});
    await refresh();
    chooseSession(created.id);
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
  const buildPreview = async () => {
    const parsed: unknown = JSON.parse(variables);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
      throw new Error('Variables must be a JSON object.');
    const saved = await savePreset();
    const current = session || (await newSession());
    const compiled = await request<Preview>('/prompts/preview', 'POST', {
      session_id: current.id,
      expected_session_revision: current.revision,
      preset_id: saved.id,
      preset_revision: saved.revision,
      config_revision: data.config.revision,
      input,
      variables: parsed,
    });
    setPreviewRevision(current.revision);
    setPreview(compiled);
    setTab('preview');
  };
  const start = async () => {
    if (!preview) return;
    // The preview ID is stable across a lost response and a user's explicit retry.
    const created = await request<Run>(
      '/runs',
      'POST',
      {
        preview_id: preview.id,
        input_hash: preview.input_hash,
        expected_session_revision: previewRevision,
      },
      preview.id,
    );
    setSelectedRun(created.id);
    setInput('');
    setPreview(null);
    setTab('trace');
    await refresh();
  };
  const changeInput = (text: string) => {
    setInput(text);
    setPreview(null);
  };
  return (
    <div className="studio">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Security Agent Studio home">
          <span className="brand-icon">
            <Code2 size={20} />
          </span>
          <span>
            Security Agent<span className="brand-light"> Studio</span>
          </span>
        </a>
        <span className="topbar-divider" />
        <span className="workspace-label">Local workspace</span>
        <div className="topbar-actions">
          <span className="connection">
            <span className="dot" />
            Connected
          </span>
          <button className="button small" disabled={busy} onClick={() => setSettings(true)}>
            <SlidersHorizontal size={14} />
            Settings
          </button>
          <button
            className="icon-button"
            title="Lock workspace"
            aria-label="Lock workspace"
            onClick={() =>
              void perform(async () => {
                await request('/auth/session', 'DELETE');
                cache.clear();
              })
            }
          >
            <LockKeyhole size={15} />
          </button>
        </div>
      </header>
      <div className="workspace-bar">
        <div className="row">
          <span className="eyebrow">WORKBENCH</span>
          <ChevronRight size={13} />
          <MessageSquare size={14} />
          <select
            aria-label="Conversation"
            disabled={busy}
            value={session?.id || ''}
            onChange={(event) => chooseSession(event.target.value)}
          >
            {!data.sessions.length && <option value="">New investigation</option>}
            {data.sessions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
          <button
            className="icon-button"
            aria-label="New conversation"
            title="New conversation"
            disabled={busy}
            onClick={() =>
              void perform(async () => {
                await newSession();
              })
            }
          >
            <Plus size={15} />
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
              <Trash2 size={14} />
            </button>
          )}
        </div>
        <span
          className={`provider-pill ${data.config.provider.adapter === 'fake' ? 'simulated' : ''}`}
        >
          <span className="dot" />
          {data.config.provider.adapter === 'fake' ? 'Simulated model' : data.config.provider.model}
        </span>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}>
            <X size={14} />
          </button>
        </div>
      )}
      <main className="workbench">
        <Composer
          preset={preset}
          presets={data.presets}
          dirty={dirty}
          busy={busy}
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
        <section className="conversation" aria-label="Conversation workspace">
          <div className="conversation-heading">
            <div>
              <span className="section-label">INVESTIGATION</span>
              <h1>
                {session && session.revision > 0 ? session.title : 'From instruction to evidence.'}
              </h1>
            </div>
            {run && (
              <span className={`status status-${run.status}`} data-testid="run-status">
                {run.status.replaceAll('_', ' ')}
              </span>
            )}
          </div>
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
          <div className="conversation-scroll" aria-live="polite">
            {runQuery.isError && (
              <p role="alert" className="error-text">
                {runQuery.error.message}
              </p>
            )}
            {run ? (
              <>
                <div className="task-message">
                  <span className="message-caption">YOUR TASK</span>
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
                        <pre>{message.content}</pre>
                      </details>
                    ) : (
                      <>
                        <span className="message-caption">
                          {message.role === 'assistant' ? 'AGENT' : message.role.toUpperCase()}
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
                    <span className="message-caption">AGENT · STREAMING</span>
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
              <div className="conversation-empty">
                <div className="empty-orbit">
                  <Code2 size={30} strokeWidth={1.4} />
                </div>
                <span className="eyebrow">YOUR NEXT INVESTIGATION STARTS HERE</span>
                <h2>
                  A clear prompt.
                  <br />A traceable result.
                </h2>
                <p>
                  Arrange your instructions on the left.
                  <br />
                  Preview the request, then put your agent to work.
                </p>
                <div className="starter-cards">
                  <button onClick={() => changeInput('/read README.md')}>
                    <FileText size={18} />
                    <span>
                      Explore a local file<small>Read README.md with a real tool</small>
                    </span>
                    <ArrowRight size={14} />
                  </button>
                  <button onClick={() => changeInput('/echo hello')}>
                    <ShieldCheck size={18} />
                    <span>
                      Try tool approval<small>Inspect, approve, and continue</small>
                    </span>
                    <ArrowRight size={14} />
                  </button>
                </div>
              </div>
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
              placeholder="Describe your task, or try /read README.md"
              rows={3}
              disabled={!!active || busy}
            />
            <details className="task-variables">
              <summary>
                Input variables <span className="subtle">JSON</span>
              </summary>
              <textarea
                aria-label="Input variables JSON"
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
              <span className="subtle">
                {preview
                  ? 'Preview ready · input frozen'
                  : `${session?.messages.length || 0} history messages`}
              </span>
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
                      disabled={busy || !preview}
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
        <Inspector key={run?.id || 'empty'} preview={preview} run={run} tab={tab} onTab={setTab} />
      </main>
      <footer className="statusbar">
        <span>
          <span className="dot" />
          Workspace ready
        </span>
        <span className="workspace-path" title={data.workspace_root}>
          {data.workspace_root}
        </span>
        <span>
          Single agent<span className="footer-dot">·</span>Local SQLite
          <span className="footer-dot">·</span>v0.1.0
        </span>
      </footer>
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
