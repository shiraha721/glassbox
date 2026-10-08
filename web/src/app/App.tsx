import { Fragment, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArchiveRestore,
  Bell,
  BellOff,
  Bot,
  Code2,
  Eye,
  FlaskConical,
  FolderOpen,
  GitCompare,
  ImagePlus,
  Layers,
  PanelLeft,
  Pencil,
  Play,
  SlidersHorizontal,
  Square,
  Terminal,
  Wrench,
  X,
} from 'lucide-react';
import {
  ApiError,
  errorMessage,
  request,
  terminal,
  workspacePath,
  type Attachment,
  type Bootstrap,
  type ContextView,
  type Preset,
  type WorkspaceView,
  type Run,
  type RunRequest,
  type Session,
} from '../api/client';
import { useRunEvents } from '../api/events';
import { Composer } from '../features/prompts/Composer';
import { ConversationHistory } from '../features/history/ConversationHistory';
import { Inspector } from '../features/inspection/Inspector';
import { RunTurn } from '../features/chat/ConversationTurn';
import { CompactionMarker } from '../features/chat/CompactionMarker';
import { compactionsAfter } from '../features/chat/compactions';
import { Settings } from '../features/settings/Settings';
import { Evaluations } from '../features/evaluation/Evaluations';
import { WorkspacePanel } from '../features/workspace/WorkspacePanel';
import { Projects } from '../features/projects/Projects';
import { usePresetDrafts } from '../features/prompts/usePresetDrafts';
import { useLivePreview } from '../features/prompts/useLivePreview';
import { ToolsPanel } from '../features/tools/ToolCatalog';
import { ContextPanel } from '../features/chat/ContextPanel';
import { ProcessesPanel } from '../features/chat/ProcessesPanel';
import { useNotificationSetting, useRunNotifications } from '../features/chat/notifications';
import { StatusBadge } from '../shared/StatusBadge';
import { ModelPicker } from '../features/composer/ModelPicker';
import { AccessPicker } from '../features/composer/AccessPicker';
import { useRunChoice } from '../features/composer/useRunChoice';
import { effectiveConfig } from '../features/composer/choices';
import type { SettingsPage } from '../features/settings/pages';
import { TaskInput } from '../features/composer/TaskInput';
import { sendKeyLabel, useSendKey } from '../features/composer/sendKey';
import { GitPanel } from '../features/workspace/GitPanel';
import { AttachmentStrip } from '../features/composer/AttachmentStrip';
import { addImages, IMAGE_TYPES, imageFiles, uploadImages } from '../features/composer/attachments';

type Panel = 'prompt' | 'inspector' | 'workspace' | 'tools' | 'context' | 'processes' | 'changes';

const emptyRuns: Run[] = [];
const starters = [
  'Summarize how this project is organized and how to run its tests.',
  'Find the cause of the failing tests and fix it.',
  'Review the code under src/ for security issues and report evidence.',
];

/** Load the local workspace directly, without accounts or an unlock screen. */
export default function App() {
  const query = useQuery({
    queryKey: ['bootstrap'],
    queryFn: () => request<Bootstrap>('/bootstrap'),
    refetchInterval: (state) =>
      state.state.data?.sessions.some((item) => !!item.compacting_id) ? 1000 : false,
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
  const [{ sessionId, projectId }, setSelection] = useState(() => {
    const remembered = data.sessions.find(
      (item) => item.id === localStorage.getItem('studio.session'),
    );
    const storedProject = localStorage.getItem('studio.project') || '';
    return {
      sessionId: remembered?.id || '',
      projectId: remembered
        ? remembered.project_id || ''
        : data.projects.some((item) => item.id === storedProject)
          ? storedProject
          : '',
    };
  });
  const [selectedRun, setSelectedRun] = useState('');
  const [input, setInput] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [variables, setVariables] = useState('{}');
  const [tab, setTab] = useState<'preview' | 'trace'>(() =>
    localStorage.getItem('studio.session') ? 'trace' : 'preview',
  );
  const [settings, setSettings] = useState<SettingsPage | null>(null);
  const [evaluations, setEvaluations] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(
    () => window.matchMedia('(min-width: 761px)').matches,
  );
  const [panel, setPanel] = useState<Panel | null>(null);
  const [skills, setSkills] = useState<string[]>([]);
  const submission = useRef<{ fingerprint: string; key: string; body: RunRequest } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const project = data.projects.find((item) => item.id === projectId);
  const projectSessions = data.sessions.filter((item) => (item.project_id || '') === projectId);
  // An empty selection is a new draft, never implicit permission to continue an old conversation.
  const session = projectSessions.find((item) => item.id === sessionId);
  const conversationScroll = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const runQuery = useQuery({
    queryKey: ['runs', session?.id],
    enabled: !!session,
    queryFn: () => request<Run[]>(`/sessions/${session!.id}/runs`),
    // Events refresh the active Run; this slower poll only covers a missed event stream.
    refetchInterval: (query) =>
      query.state.data?.some((run) => !terminal(run.status)) ? 3000 : false,
  });
  const runs = runQuery.data || emptyRuns;
  // Compactions appear in the conversation where the history they summarize ends.
  const contextQuery = useQuery({
    queryKey: ['context', session?.id],
    enabled: !!session && runs.length > 0,
    queryFn: () => request<ContextView>(`/sessions/${session!.id}/context`),
    refetchInterval: (query) => (query.state.data?.compacting_id ? 1000 : false),
  });
  const markers = compactionsAfter(runs, contextQuery.data?.compactions ?? []);
  const compactionState = `${session?.compacting_id ?? ''}:${session?.compaction_id ?? ''}`;
  const sessionKey = session?.id;
  useEffect(() => {
    // A summary that starts, finishes or is restored changes the session first.
    if (sessionKey) void cache.invalidateQueries({ queryKey: ['context', sessionKey] });
  }, [compactionState, sessionKey, cache]);
  const [choice, setChoice] = useRunChoice(data, session?.id || '', runs, runQuery.isSuccess);
  // An unset model sends no choice, so the server explains what to configure.
  const runChoice = {
    model: choice.model.model ? choice.model : null,
    access: choice.access,
  };
  const run = runs.find((item) => item.id === selectedRun) || runs.at(-1);
  const active = runs.find((item) => !terminal(item.status));
  const latestRun = active || runs.at(-1);
  const workspaceRoot = project?.workspace_root || data.workspace_root;
  // The preview compiles whenever it is on screen; the Preview button only opens it.
  const previewVisible = panel === 'inspector' && tab === 'preview';
  const workspaceQuery = useQuery({
    queryKey: ['workspace', projectId],
    queryFn: () => request<WorkspaceView>(workspacePath('', projectId)),
    enabled: panel === 'prompt' || panel === 'workspace' || previewVisible,
    refetchOnWindowFocus: false,
  });
  const inspection = useLivePreview(
    previewVisible && !active,
    JSON.stringify({
      session_id: session?.id || null,
      project_id: projectId || null,
      expected_session_revision: session?.revision ?? null,
      preset,
      config_revision: data.config.revision,
      input,
      variables,
      skills,
      attachments: attachments.map((item) => item.hash),
      ...runChoice,
    }),
    workspaceQuery.dataUpdatedAt,
  );
  useRunEvents(active);
  const autoCompact = !!data.config.auto_compact?.enabled;
  const endedRun = latestRun && terminal(latestRun.status) ? latestRun.id : '';
  useEffect(() => {
    if (!autoCompact || !endedRun) return;
    // Automatic compaction starts just after a run ends, possibly after the refresh the end
    // triggered. Look again so its progress, and the revision it activates, reach the page.
    const timers = [1000, 3000].map((delay) =>
      setTimeout(() => {
        void cache.invalidateQueries({ queryKey: ['bootstrap'] });
        void cache.invalidateQueries({ queryKey: ['context'] });
      }, delay),
    );
    return () => timers.forEach(clearTimeout);
  }, [autoCompact, endedRun, cache]);
  const notifications = useNotificationSetting();
  useRunNotifications(latestRun, session?.title || 'Conversation', notifications.enabled);
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
      cache.invalidateQueries({ queryKey: ['context'] }),
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
    const chosen = data.sessions.find((item) => item.id === id);
    const nextProject = chosenProject ?? (chosen ? chosen.project_id || '' : projectId);
    setSelection({ sessionId: id, projectId: nextProject });
    localStorage.setItem('studio.session', id);
    localStorage.setItem('studio.project', nextProject);
    setSkills([]);
    setVariables('{}');
    submission.current = null;
    followLatest.current = true;
    if (window.matchMedia('(max-width: 760px)').matches) setSidebarOpen(false);
    setSelectedRun('');
    setPanel(null);
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
  const inputRequest = async (): Promise<RunRequest> => {
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
      variables: parsed as Record<string, unknown>,
      skills,
      attachments: attachments.map((item) => item.hash),
      ...runChoice,
    };
  };
  const openPreview = () => {
    setTab('preview');
    setPanel('inspector');
  };
  const start = async () => {
    // New submissions compile current input; transport retries retain their accepted snapshot.
    const fingerprint = (id: string) =>
      JSON.stringify({
        session_id: id,
        projectId,
        input,
        variables,
        skills,
        attachments,
        choice,
        preset: { ...preset, revision: 0 },
      });
    let pending = submission.current;
    if (!pending || pending.fingerprint !== fingerprint(session?.id || '')) {
      const body = await inputRequest();
      pending = { fingerprint: fingerprint(body.session_id), key: crypto.randomUUID(), body };
      submission.current = pending;
    }
    let created: Run;
    try {
      // Server completion can advance revisions before a lost response is retried. Keep the
      // original body and key together, rather than rebuilding it from newer history versions.
      created = await request<Run>('/runs', 'POST', pending.body, pending.key);
    } catch (caught) {
      if (caught instanceof ApiError && [400, 403, 404, 409, 422].includes(caught.status)) {
        submission.current = null;
        await refresh();
      }
      throw caught;
    }
    submission.current = null;
    // Publish the accepted projection immediately. Showing the previous turn's success while
    // the next Run is being fetched makes both users and controls act on the wrong lifecycle.
    cache.setQueryData<Run[]>(['runs', created.session_id], (current = []) =>
      current.some((item) => item.id === created.id)
        ? current.map((item) => (item.id === created.id ? created : item))
        : [...current, created],
    );
    followLatest.current = true;
    setSelectedRun(created.id);
    setInput('');
    setAttachments([]);
    setTab('trace');
    setPanel(null);
    await refresh();
  };
  const canRun = !busy && !uploading && !!input.trim() && !active && !session?.compacting_id;
  const attach = (files: FileList | File[] | null | undefined) => {
    const images = imageFiles(files);
    if (!images.length) return;
    setUploading(true);
    void uploadImages(images)
      .then((stored) => setAttachments((current) => addImages(current, stored)))
      .catch((failure) => setError(errorMessage(failure)))
      .finally(() => setUploading(false));
  };
  const sendKey = useSendKey();
  const togglePanel = (name: Panel) => setPanel(panel === name ? null : name);
  const panelButton = (name: Panel, label: string, icon: React.ReactNode, disabled = false) => (
    <button
      className="panel-toggle"
      disabled={disabled}
      aria-pressed={panel === name}
      title={label}
      onClick={() => togglePanel(name)}
    >
      {icon}
      <span className="toggle-label">{label}</span>
    </button>
  );
  return (
    <div className="studio">
      <aside
        className="sidebar"
        id="conversation-sidebar"
        aria-label="Conversations"
        hidden={!sidebarOpen}
      >
        <div className="sidebar-heading">
          <span className="brand-mark" aria-hidden>
            <Code2 size={15} />
          </span>
          <strong>Agent Studio</strong>
          <button
            className="icon-button"
            aria-label="Close sidebar"
            onClick={() => setSidebarOpen(false)}
          >
            <PanelLeft size={16} />
          </button>
        </div>
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
            chooseSession('', id);
          }}
        />
        <ConversationHistory
          workspace={project?.name || 'Default workspace'}
          onNew={() =>
            void perform(async () => {
              await newSession();
            })
          }
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
            aria-pressed={panel === 'workspace'}
            onClick={() => togglePanel('workspace')}
          >
            <FolderOpen size={16} /> Workspace
          </button>
          {notifications.supported && (
            <button
              className="nav-button"
              aria-pressed={notifications.enabled}
              disabled={notifications.blocked}
              title={
                notifications.blocked
                  ? 'Notifications are blocked in this browser’s site settings.'
                  : 'Notify when a run needs approval or ends while this tab is in the background.'
              }
              onClick={() => void notifications.toggle()}
            >
              {notifications.enabled ? <Bell size={16} /> : <BellOff size={16} />} Notifications
            </button>
          )}
          <button className="nav-button" disabled={busy} onClick={() => setEvaluations(true)}>
            <FlaskConical size={16} /> Evaluations
          </button>
          <button className="nav-button" disabled={busy} onClick={() => setSettings('providers')}>
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
            <div className="row tight">
              <h1 title={session?.title}>{session?.title || 'New conversation'}</h1>
              {session?.archived && <span className="badge">Archived</span>}
              {latestRun && <StatusBadge status={latestRun.status} testId="run-status" />}
            </div>
            <span className="workspace-caption" title={workspaceRoot}>
              <FolderOpen size={12} />
              {project?.name || 'Default workspace'} · {workspaceRoot}
            </span>
          </div>
          <nav className="topbar-actions" aria-label="Panels">
            {panelButton('changes', 'Changes', <GitCompare size={15} />, busy)}
            {panelButton('processes', 'Processes', <Terminal size={15} />, !session)}
            {panelButton('context', 'Context', <Layers size={15} />, !session || busy)}
            {panelButton('tools', 'Tools', <Wrench size={15} />, busy)}
            {panelButton(
              'prompt',
              'Prompt',
              <span className="toggle-icon">
                <SlidersHorizontal size={15} />
                {dirty && <span className="dirty-dot" title="Unsaved prompt changes" />}
              </span>,
              busy,
            )}
            {panelButton('inspector', 'Inspector', <Eye size={15} />, busy)}
          </nav>
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
                  <Fragment key={turn.id}>
                    <RunTurn
                      run={turn}
                      index={index}
                      busy={busy}
                      selected={turn.id === run?.id && panel === 'inspector'}
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
                    {markers.get(turn.id)?.map((item) => (
                      <CompactionMarker
                        key={item.id}
                        item={item}
                        active={item.id === contextQuery.data?.active_compaction_id}
                        onOpen={() => setPanel('context')}
                      />
                    ))}
                  </Fragment>
                ))
              ) : (
                <div className="conversation-empty">
                  <span className="empty-agent">
                    <Bot size={24} />
                  </span>
                  <h2>What should the agent work on?</h2>
                  <p>
                    The agent reads and edits files in{' '}
                    <code title={workspaceRoot}>{project?.name || 'the default workspace'}</code>{' '}
                    and runs commands in an isolated sandbox without network access.
                  </p>
                  {!session?.archived && (
                    <div className="starter-list">
                      {starters.map((text) => (
                        <button
                          key={text}
                          className="starter"
                          disabled={busy}
                          onClick={() => {
                            setInput(text);
                            document.getElementById('task-input')?.focus();
                          }}
                        >
                          {text}
                        </button>
                      ))}
                    </div>
                  )}
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
                <div
                  className="task-composer"
                  onDragOver={(event) => {
                    if (event.dataTransfer.types.includes('Files')) event.preventDefault();
                  }}
                  onDrop={(event) => {
                    if (!event.dataTransfer.files.length) return;
                    event.preventDefault();
                    if (!active && !busy) attach(event.dataTransfer.files);
                  }}
                >
                  <AttachmentStrip
                    items={attachments}
                    onRemove={(item) =>
                      setAttachments((current) => current.filter((other) => other !== item))
                    }
                  />
                  <label className="sr-only" htmlFor="task-input">
                    Task
                  </label>
                  <TaskInput
                    value={input}
                    onChange={setInput}
                    projectId={projectId}
                    sendKey={sendKey}
                    disabled={!!active || busy}
                    placeholder={
                      active ? 'The agent is working…' : 'Describe a task for the agent…'
                    }
                    onSend={() => {
                      if (canRun) void perform(start);
                    }}
                    onPaste={(event) => {
                      const images = imageFiles(event.clipboardData.files);
                      if (images.length) {
                        event.preventDefault();
                        attach(images);
                      }
                    }}
                  />
                  {skills.length > 0 && (
                    <div className="selected-skills">
                      Skills: {skills.map((name) => `$${name}`).join(', ')}
                    </div>
                  )}
                  <details className="task-variables">
                    <summary>Input variables</summary>
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
                    <div className="composer-context">
                      <label
                        className="icon-button attach-button"
                        title="Attach images (or paste or drop them)"
                        aria-disabled={!!active || busy || uploading}
                      >
                        <ImagePlus size={15} />
                        <input
                          type="file"
                          className="sr-only"
                          aria-label="Attach images"
                          accept={IMAGE_TYPES.join(',')}
                          multiple
                          disabled={!!active || busy || uploading}
                          onChange={(event) => {
                            attach(event.target.files);
                            event.target.value = '';
                          }}
                        />
                      </label>
                      <select
                        className="preset-select"
                        aria-label="Prompt preset"
                        title="Prompt preset"
                        value={preset.id}
                        disabled={busy || !!active}
                        onChange={(event) => {
                          const next = drafts.presets.find(
                            (item) => item.id === event.target.value,
                          );
                          if (next) drafts.select(next);
                        }}
                      >
                        {drafts.presets.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                            {item.id === preset.id && dirty ? ' (unsaved)' : ''}
                          </option>
                        ))}
                      </select>
                      <button
                        className="icon-button"
                        aria-label="Edit prompt"
                        title="Edit prompt blocks"
                        onClick={() => setPanel('prompt')}
                      >
                        <Pencil size={14} />
                      </button>
                      <ModelPicker
                        data={data}
                        choice={choice.model}
                        disabled={busy || !!active}
                        onChange={(model) => setChoice({ ...choice, model })}
                        onManage={() => setSettings('models')}
                        onRefreshed={refresh}
                      />
                      <AccessPicker
                        access={choice.access}
                        disabled={busy || !!active}
                        onChange={(access) => setChoice({ ...choice, access })}
                      />
                    </div>
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
                            disabled={busy}
                            title="Inspect the exact model input without running"
                            onClick={openPreview}
                          >
                            <Eye size={14} />
                            Preview
                          </button>
                          <button
                            className="button primary"
                            disabled={!canRun}
                            title={`Run (${sendKeyLabel(sendKey)})`}
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
                <div className="input-hint">
                  {sendKey === 'enter' ? (
                    <>
                      <kbd>Enter</kbd> to run · <kbd>Shift</kbd> + <kbd>Enter</kbd> for a new line
                    </>
                  ) : (
                    <>
                      <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to run
                    </>
                  )}{' '}
                  · <kbd>/</kbd> commands · <kbd>$</kbd> skills
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
              onImported={async (value) => {
                await refresh();
                drafts.select(value);
              }}
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
              visible={panel === 'inspector'}
              preview={inspection.preview}
              previewUpdating={inspection.updating}
              previewError={inspection.error}
              previewPaused={!!active}
              runs={runs}
              run={run}
              onRun={(id) => {
                setSelectedRun(id);
                followLatest.current = id === runs.at(-1)?.id;
                document
                  .getElementById(`turn-${id}`)
                  ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }}
              tab={tab}
              onTab={setTab}
              onClose={() => setPanel(null)}
            />
          </div>
          {panel === 'context' && session && (
            <div className="utility-pane">
              <ContextPanel
                key={session.id}
                session={session}
                config={data.config}
                model={runChoice.model}
                onClose={() => setPanel(null)}
                onChanged={refresh}
              />
            </div>
          )}
          {panel === 'processes' && session && (
            <div className="utility-pane">
              <ProcessesPanel key={session.id} session={session} onClose={() => setPanel(null)} />
            </div>
          )}
          {panel === 'tools' && (
            <div className="utility-pane">
              <ToolsPanel
                tools={data.tools}
                config={effectiveConfig(data.config, data.tools, choice.access)}
                access={choice.access}
                grants={session?.grants ?? []}
                onRevoke={(remaining) =>
                  void perform(async () => {
                    await request(`/sessions/${session!.id}`, 'PATCH', {
                      expected_revision: session!.revision,
                      grants: remaining,
                    });
                    await refresh();
                  })
                }
                onClose={() => setPanel(null)}
                onSettings={() => {
                  setPanel(null);
                  setSettings('tools');
                }}
              />
            </div>
          )}
          {panel === 'changes' && (
            <div className="utility-pane">
              <GitPanel
                key={projectId}
                projectId={projectId}
                onClose={() => setPanel(null)}
                onReview={(task) => {
                  const reviewer = drafts.presets.find((item) => item.id === 'code-review');
                  if (reviewer) drafts.select(reviewer);
                  setInput(task);
                  setPanel(null);
                  document.getElementById('task-input')?.focus();
                }}
              />
            </div>
          )}
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
      {evaluations && (
        <Evaluations
          data={data}
          projectId={projectId}
          onClose={() => setEvaluations(false)}
          onChanged={refresh}
        />
      )}
      {settings && (
        <Settings
          data={data}
          page={settings}
          onClose={() => setSettings(null)}
          onSaved={async () => {
            submission.current = null;
            await refresh();
          }}
        />
      )}
    </div>
  );
}
