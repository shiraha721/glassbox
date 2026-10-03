import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  ArchiveRestore,
  Bot,
  Code2,
  Eye,
  FolderOpen,
  GitBranch,
  PanelLeft,
  Play,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Terminal,
  User,
  X,
} from 'lucide-react';
import Markdown, { type Components } from 'react-markdown';
import {
  errorMessage,
  request,
  terminal,
  workspacePath,
  type Bootstrap,
  type Preset,
  type WorkspaceView,
  type ResumeRequest,
  type Run,
  type Session,
} from './api/client';
import { useRunEvents } from './api/events';
import { Composer } from './Composer';
import { ConversationHistory } from './ConversationHistory';
import { Inspector, Json } from './Inspector';
import { Settings } from './Settings';
import { WorkspacePanel } from './WorkspacePanel';
import { Projects } from './Projects';
import { usePresetDrafts } from './usePresetDrafts';
import { useLivePreview } from './useLivePreview';

const emptyRuns: Run[] = [];

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

/** Coordinate server state, reversible prompt drafts, and live inspection of current input. */
function Workspace({ data }: { data: Bootstrap }) {
  const cache = useQueryClient();
  const drafts = usePresetDrafts(data.presets);
  const { preset, dirty } = drafts;
  const [sessionId, setSessionId] = useState(() => localStorage.getItem('studio.session') || '');
  const [projectId, setProjectId] = useState(
    () =>
      data.sessions.find((item) => item.id === localStorage.getItem('studio.session'))
        ?.project_id ||
      localStorage.getItem('studio.project') ||
      '',
  );
  const [selectedRun, setSelectedRun] = useState('');
  const [input, setInput] = useState('');
  const [variables, setVariables] = useState('{}');
  const [previewEnabled, setPreviewEnabled] = useState(false);
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
  const project = data.projects.find((item) => item.id === projectId);
  const projectSessions = data.sessions.filter((item) => (item.project_id || '') === projectId);
  const session =
    projectSessions.find((item) => item.id === sessionId) ||
    projectSessions.find((item) => !item.archived);
  const conversationScroll = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const runQuery = useQuery({
    queryKey: ['runs', session?.id],
    enabled: !!session,
    queryFn: () => request<Run[]>(`/sessions/${session!.id}/runs`),
    refetchInterval: (query) =>
      query.state.data?.some((run) => !terminal(run.status)) ? 2000 : false,
  });
  const runs = runQuery.data || emptyRuns;
  const run = runs.find((item) => item.id === selectedRun) || runs.at(-1);
  const active = runs.find((item) => !terminal(item.status));
  const latestRun = active || runs.at(-1);
  const workspaceQuery = useQuery({
    queryKey: ['workspace', projectId],
    queryFn: () => request<WorkspaceView>(workspacePath('', projectId)),
    enabled: panel === 'prompt' || panel === 'workspace' || previewEnabled,
    refetchOnWindowFocus: false,
  });
  const inspection = useLivePreview(
    previewEnabled && !active,
    JSON.stringify({
      session_id: session?.id || null,
      project_id: projectId || null,
      expected_session_revision: session?.revision ?? null,
      preset,
      config_revision: data.config.revision,
      input,
      variables,
      skills,
    }),
    workspaceQuery.dataUpdatedAt,
  );
  useRunEvents(active?.id, !!active);
  useEffect(() => {
    const root = conversationScroll.current;
    if (root && followLatest.current) root.scrollTop = root.scrollHeight;
  }, [runs]);
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ['bootstrap'] }),
      cache.invalidateQueries({ queryKey: ['runs'] }),
      cache.invalidateQueries({ queryKey: ['trace'] }),
      cache.invalidateQueries({ queryKey: ['workspace'] }),
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
  const chooseSession = (id: string, chosenProject?: string) => {
    localStorage.setItem('studio.session', id);
    setSessionId(id);
    const chosen = data.sessions.find((item) => item.id === id);
    if (chosen || chosenProject !== undefined) {
      const nextProject = chosenProject ?? chosen?.project_id ?? '';
      setProjectId(nextProject);
      localStorage.setItem('studio.project', nextProject);
    }
    setSkills([]);
    followLatest.current = true;
    if (window.matchMedia('(max-width: 760px)').matches) setSidebarOpen(false);
    setSelectedRun('');
    setPanel(null);
    setPreviewEnabled(false);
    setInput('');
    setError('');
  };
  const newSession = async (keepDraft = false) => {
    const created = await request<Session>('/sessions', 'POST', { project_id: projectId || null });
    await refresh();
    chooseSession(created.id, created.project_id || '');
    if (keepDraft) setInput(input);
    return created;
  };
  const savePreset = async (): Promise<Preset> => {
    if (!dirty) return preset;
    const saved = await request<Preset>(`/presets/${preset.id}`, 'PUT', preset);
    drafts.commit(saved);
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
  const buildPreview = () => {
    setPreviewEnabled(true);
    setTab('preview');
    setPanel('inspector');
  };
  const start = async () => {
    // Live inspection is a draft, so Run always compiles the latest input after saving edits.
    const body = await inputRequest();
    const fingerprint = JSON.stringify(body);
    // An explicit retry of unchanged input keeps its identity even if the response was lost.
    if (submission.current?.fingerprint !== fingerprint)
      submission.current = { fingerprint, key: crypto.randomUUID() };
    const created = await request<Run>('/runs', 'POST', body, submission.current.key);
    submission.current = null;
    followLatest.current = true;
    setSelectedRun(created.id);
    setInput('');
    setPreviewEnabled(false);
    setTab('trace');
    setPanel(null);
    await refresh();
  };
  const changeInput = (text: string) => {
    setInput(text);
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
        <Projects
          projects={data.projects}
          selected={projectId}
          defaultRoot={data.workspace_root}
          busy={busy}
          error={error}
          onAction={perform}
          onUpdated={refresh}
          onSelect={(id) => {
            if (id === projectId) return;
            setProjectId(id);
            localStorage.setItem('studio.project', id);
            chooseSession('', id);
          }}
        />
        <ConversationHistory
          projects={data.projects}
          sessions={projectSessions}
          folders={data.folders}
          selected={session?.id}
          busy={busy}
          error={error}
          onChoose={chooseSession}
          onAction={perform}
          onUpdated={async () => {
            await refresh();
          }}
          onRemoved={(id) => {
            if (id === session?.id) chooseSession('');
          }}
        />
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
          <div className="conversation-heading">
            <h1 title={session?.title}>{session?.title || 'New conversation'}</h1>
            <span
              className="workspace-caption"
              title={project?.workspace_root || data.workspace_root}
            >
              {project?.name || 'Default workspace'} ·{' '}
              {project?.workspace_root || data.workspace_root}
            </span>
          </div>
          {session?.archived && <span className="badge">Archived</span>}
          {latestRun && (
            <span className={`status status-${latestRun.status}`} data-testid="run-status">
              {latestRun.status.replaceAll('_', ' ')}
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
            {runs.length > 0 && (
              <div className="conversation-toolbar">
                <label htmlFor="run-selector">
                  {runs.length} {runs.length === 1 ? 'turn' : 'turns'}
                </label>
                <select
                  id="run-selector"
                  aria-label="Run history"
                  value={run?.id || ''}
                  onChange={(event) => {
                    setSelectedRun(event.target.value);
                    setTab('trace');
                    followLatest.current = event.target.value === runs.at(-1)?.id;
                    document
                      .getElementById(`turn-${event.target.value}`)
                      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                  }}
                >
                  {runs.map((item, index) => (
                    <option key={item.id} value={item.id}>
                      #{index + 1} · {item.input.slice(0, 45)} · {item.status}
                    </option>
                  ))}
                </select>
                <button
                  className="button small quiet"
                  disabled={busy}
                  onClick={() => {
                    setTab('trace');
                    setPanel('inspector');
                  }}
                >
                  Inspect turn
                </button>
              </div>
            )}
            <div
              className="conversation-scroll"
              ref={conversationScroll}
              aria-live="polite"
              onScroll={(event) => {
                const root = event.currentTarget;
                followLatest.current = root.scrollHeight - root.scrollTop - root.clientHeight < 100;
              }}
            >
              {runQuery.isError && (
                <p role="alert" className="error-text">
                  {runQuery.error.message}
                </p>
              )}
              {runs.length ? (
                runs.map((turn, index) => (
                  <RunTurn
                    key={turn.id}
                    run={turn}
                    index={index}
                    busy={busy}
                    selected={turn.id === run?.id}
                    canFork={!session?.active_run_id && turn.status === 'succeeded'}
                    onInspect={() => {
                      setSelectedRun(turn.id);
                      setTab('trace');
                      setPanel('inspector');
                    }}
                    onFork={() =>
                      void perform(async () => {
                        const forked = await request<Session>(
                          `/sessions/${session!.id}/copy`,
                          'POST',
                          {
                            expected_revision: session!.revision,
                            operation: 'fork',
                            through_run_id: turn.id,
                          },
                        );
                        await refresh();
                        chooseSession(forked.id);
                      })
                    }
                    onApprove={(decision) =>
                      void perform(async () => {
                        await request(
                          `/runs/${turn.id}/approvals/${String(turn.approval!.id)}`,
                          'POST',
                          { decision },
                        );
                        await refresh();
                      })
                    }
                    onResume={(resolutions) =>
                      void perform(async () => {
                        await request(`/runs/${turn.id}/resume`, 'POST', { resolutions });
                        await refresh();
                      })
                    }
                  />
                ))
              ) : (
                <div className="conversation-empty">
                  <span className="empty-agent">
                    <Bot size={25} />
                  </span>
                  <h2>New conversation</h2>
                  <p>Send a message to start working.</p>
                  <button className="button small quiet" onClick={() => setPanel('prompt')}>
                    Edit prompt
                  </button>
                </div>
              )}
            </div>
            {session?.archived ? (
              <div className="archived-dock">
                <ArchiveRestore size={17} />
                <span>This conversation is archived.</span>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      await request(`/sessions/${session.id}`, 'PATCH', {
                        expected_revision: session.revision,
                        archived: false,
                      });
                      await refresh();
                    })
                  }
                >
                  Restore conversation
                </button>
              </div>
            ) : (
              <div className="task-dock">
                <div className="task-composer">
                  <div className="task-context">
                    <span>Message</span>
                    <button className="text-button" onClick={() => setPanel('prompt')}>
                      Preset: {preset.name}
                      {dirty ? ' · unsaved' : ''}
                    </button>
                  </div>
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
                          <button className="button" disabled={busy} onClick={buildPreview}>
                            <Eye size={14} />
                            Preview
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
                  <div className="input-hint">Ctrl+Enter to run</div>
                </div>
              </div>
            )}
          </section>
          {/* Keep editors mounted so toggling a panel does not discard unapplied rule drafts. */}
          <div className="utility-pane" hidden={panel !== 'prompt'}>
            <Composer
              preset={preset}
              presets={drafts.presets}
              projectId={projectId}
              workspace={workspaceQuery.data}
              workspaceError={workspaceQuery.error?.message}
              selectedSkills={skills}
              input={input}
              onSkills={setSkills}
              onWorkspace={() => setPanel('workspace')}
              onRefreshContext={() => void workspaceQuery.refetch()}
              dirty={dirty}
              busy={busy}
              onClose={() => setPanel(null)}
              onChange={(value, group) => {
                drafts.change(value, group);
              }}
              onSelect={(value) => {
                drafts.select(value);
              }}
              canUndo={drafts.canUndo}
              canRedo={drafts.canRedo}
              onUndo={() => {
                drafts.undo();
              }}
              onRedo={() => {
                drafts.redo();
              }}
              onDiscard={() => {
                drafts.discard();
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
              preview={inspection.preview}
              previewUpdating={inspection.updating}
              previewError={inspection.error}
              run={run}
              tab={tab}
              onTab={setTab}
              onClose={() => setPanel(null)}
            />
          </div>
          {panel === 'workspace' && (
            <div className="utility-pane">
              <WorkspacePanel
                key={projectId}
                projectId={projectId}
                selected={skills}
                onSelect={(value) => {
                  setSkills(value);
                }}
                onClose={() => setPanel(null)}
                onChanged={() => void cache.invalidateQueries({ queryKey: ['workspace'] })}
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
            await refresh();
          }}
        />
      )}
    </div>
  );
}

/** Render every persisted turn, with tools and continuation controls next to their evidence. */
function RunTurn({
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
