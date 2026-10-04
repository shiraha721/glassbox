import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Archive,
  ArchiveRestore,
  ChevronDown,
  Copy,
  FolderInput,
  FolderPlus,
  GitBranch,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import {
  request,
  type Folder,
  type Project,
  type Run,
  type Session,
  type SessionUpdate,
} from './api/client';
import { useDialogFocus } from './useDialogFocus';

type Dialog =
  | { kind: 'rename' | 'move' | 'project' | 'delete' | 'fork'; session: Session }
  | { kind: 'new-folder' }
  | { kind: 'rename-folder' | 'delete-folder'; folder: Folder };
type Menu = { x: number; y: number } & ({ session: Session } | { folder: Folder });

/** Organize local history with flat folders and explicit conversation actions. */
export function ConversationHistory({
  sessions,
  folders,
  projects,
  selected,
  busy,
  error,
  onChoose,
  onUpdated,
  onRemoved,
  onAction,
}: {
  sessions: Session[];
  folders: Folder[];
  projects: Project[];
  selected?: string;
  busy: boolean;
  error?: string;
  onChoose: (id: string, projectId?: string) => void;
  onUpdated: () => Promise<void>;
  onRemoved: (id: string) => void;
  onAction: (operation: () => Promise<void>) => Promise<void>;
}) {
  const [search, setSearch] = useState('');
  const [archived, setArchived] = useState(false);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [menuTarget, setMenu] = useState<Menu | null>(null);
  // A menu can stay open across Run completion. Its actions need the latest revision
  // and busy state, rather than the session object captured when it was opened.
  const menu =
    menuTarget &&
    ('session' in menuTarget
      ? {
          ...menuTarget,
          session: sessions.find((item) => item.id === menuTarget.session.id) || menuTarget.session,
        }
      : {
          ...menuTarget,
          folder: folders.find((item) => item.id === menuTarget.folder.id) || menuTarget.folder,
        });
  const [dialogTarget, setDialog] = useState<Dialog | null>(null);
  const dialog =
    dialogTarget &&
    ('session' in dialogTarget
      ? {
          ...dialogTarget,
          session:
            sessions.find((item) => item.id === dialogTarget.session.id) || dialogTarget.session,
        }
      : 'folder' in dialogTarget
        ? {
            ...dialogTarget,
            folder:
              folders.find((item) => item.id === dialogTarget.folder.id) || dialogTarget.folder,
          }
        : dialogTarget);
  const dialogRef = useRef<HTMLFormElement>(null);
  useDialogFocus(dialogRef, !!dialog, () => {
    if (!busy) setDialog(null);
  });
  const [value, setValue] = useState('');
  const forkSource = dialog?.kind === 'fork' ? dialog.session.id : '';
  const turns = useQuery({
    queryKey: ['fork-turns', forkSource],
    enabled: !!forkSource,
    queryFn: () => request<Run[]>(`/sessions/${forkSource}/runs`),
  });
  const forkTurns = turns.data?.filter((run) => run.status === 'succeeded') || [];
  const visible = sessions.filter(
    (item) => item.archived === archived && item.title.toLowerCase().includes(search.toLowerCase()),
  );

  const openDialog = (next: Dialog, initial = '') => {
    setMenu(null);
    setValue(initial);
    setDialog(next);
  };
  const openMenu = (element: HTMLElement, target: { session: Session } | { folder: Folder }) => {
    const box = element.getBoundingClientRect();
    setMenu({
      ...target,
      x: Math.min(box.left, window.innerWidth - 224),
      y: Math.max(8, Math.min(box.bottom + 4, window.innerHeight - 300)),
    });
  };
  const update = async (session: Session, changes: Omit<SessionUpdate, 'expected_revision'>) => {
    await request(`/sessions/${session.id}`, 'PATCH', {
      expected_revision: session.revision,
      ...changes,
    });
  };
  const copy = async (session: Session, operation: 'duplicate' | 'fork', through?: string) => {
    const created = await request<Session>(`/sessions/${session.id}/copy`, 'POST', {
      expected_revision: session.revision,
      operation,
      ...(through ? { through_run_id: through } : {}),
    });
    await onUpdated();
    setArchived(false);
    onChoose(created.id, created.project_id || '');
  };
  const submit = async () => {
    if (!dialog) return;
    if (dialog.kind === 'new-folder') await request('/folders', 'POST', { name: value.trim() });
    else if ('folder' in dialog) {
      if (dialog.kind === 'rename-folder')
        await request(`/folders/${dialog.folder.id}`, 'PATCH', {
          name: value.trim(),
          expected_revision: dialog.folder.revision,
        });
      else await request(`/folders/${dialog.folder.id}`, 'DELETE');
    } else if (dialog.kind === 'rename') await update(dialog.session, { title: value.trim() });
    else if (dialog.kind === 'move') await update(dialog.session, { folder_id: value || null });
    else if (dialog.kind === 'project') {
      await update(dialog.session, { project_id: value || null });
      await onUpdated();
      onChoose(dialog.session.id, value);
    } else if (dialog.kind === 'delete') {
      await request(`/sessions/${dialog.session.id}`, 'DELETE');
      onRemoved(dialog.session.id);
    } else if (dialog.kind === 'fork') {
      await copy(dialog.session, 'fork', value || forkTurns.at(-1)?.id);
    }
    if (!['fork', 'project'].includes(dialog.kind)) await onUpdated();
    setDialog(null);
  };
  const rows = (items: Session[]) =>
    items.map((item) => (
      <div className="session-row" key={item.id} data-selected={selected === item.id}>
        <button
          className="session-item"
          aria-current={selected === item.id ? 'page' : undefined}
          disabled={busy}
          title={item.title}
          onClick={() => onChoose(item.id)}
        >
          <MessageSquare size={14} />
          <span>{item.title}</span>
          {item.active_run_id && <span className="activity-dot" aria-label="Active run" />}
        </button>
        <button
          className="icon-button session-menu-trigger"
          aria-label={`Conversation actions for ${item.title}`}
          aria-haspopup="menu"
          disabled={busy}
          onClick={(event) => openMenu(event.currentTarget, { session: item })}
        >
          <MoreHorizontal size={16} />
        </button>
      </div>
    ));
  const title =
    dialog?.kind === 'rename'
      ? 'Rename conversation'
      : dialog?.kind === 'move'
        ? 'Move to folder'
        : dialog?.kind === 'project'
          ? 'Change workspace'
          : dialog?.kind === 'delete'
            ? 'Delete conversation'
            : dialog?.kind === 'fork'
              ? 'Fork conversation'
              : dialog?.kind === 'rename-folder'
                ? 'Rename folder'
                : dialog?.kind === 'delete-folder'
                  ? 'Delete folder'
                  : 'New folder';
  const destructive = dialog?.kind === 'delete' || dialog?.kind === 'delete-folder';
  const needsName =
    dialog?.kind === 'rename' || dialog?.kind === 'new-folder' || dialog?.kind === 'rename-folder';

  return (
    <>
      <div className="history-controls">
        <div className="history-search">
          <Search size={14} />
          <input
            aria-label="Search conversations"
            placeholder="Search conversations"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="row-between">
          <div className="history-tabs" aria-label="History filter">
            <button aria-pressed={!archived} onClick={() => setArchived(false)}>
              Active
            </button>
            <button aria-pressed={archived} onClick={() => setArchived(true)}>
              Archived
            </button>
          </div>
          <button
            className="icon-button"
            aria-label="New folder"
            title="New folder"
            disabled={busy}
            onClick={() => openDialog({ kind: 'new-folder' })}
          >
            <FolderPlus size={16} />
          </button>
        </div>
      </div>
      <nav className="session-list" aria-label="Conversation history">
        {folders.map((folder) => (
          <section className="history-group" key={folder.id}>
            <div className="history-group-heading">
              <button
                className="folder-toggle"
                aria-expanded={!collapsed.includes(folder.id)}
                onClick={() =>
                  setCollapsed(
                    collapsed.includes(folder.id)
                      ? collapsed.filter((id) => id !== folder.id)
                      : [...collapsed, folder.id],
                  )
                }
              >
                <ChevronDown size={13} />
                <span>{folder.name}</span>
                <span className="count">
                  {visible.filter((item) => item.folder_id === folder.id).length}
                </span>
              </button>
              <button
                className="icon-button"
                aria-label={`Folder actions for ${folder.name}`}
                aria-haspopup="menu"
                disabled={busy}
                onClick={(event) => openMenu(event.currentTarget, { folder })}
              >
                <MoreHorizontal size={15} />
              </button>
            </div>
            {!collapsed.includes(folder.id) &&
              rows(visible.filter((item) => item.folder_id === folder.id))}
          </section>
        ))}
        <section className="history-group">
          <div className="sidebar-label">{folders.length ? 'Unfiled' : 'Conversations'}</div>
          {rows(visible.filter((item) => !item.folder_id))}
        </section>
        {!visible.length && (
          <p className="subtle sidebar-empty">
            {search
              ? 'No matching conversations'
              : archived
                ? 'No archived conversations'
                : 'No conversations yet'}
          </p>
        )}
      </nav>
      {menu &&
        createPortal(
          <div
            className="menu-layer"
            onKeyDown={(event) => {
              if (event.key === 'Escape') setMenu(null);
            }}
          >
            <button
              className="menu-dismiss"
              aria-label="Close conversation menu"
              onClick={() => setMenu(null)}
            />
            <div className="history-menu" role="menu" style={{ left: menu.x, top: menu.y }}>
              {'session' in menu ? (
                <>
                  <button
                    role="menuitem"
                    autoFocus
                    onClick={() =>
                      openDialog({ kind: 'rename', session: menu.session }, menu.session.title)
                    }
                  >
                    <Pencil size={15} /> Rename conversation
                  </button>
                  <button
                    role="menuitem"
                    disabled={!!menu.session.active_run_id}
                    onClick={() => {
                      const source = menu.session;
                      setMenu(null);
                      void onAction(() => copy(source, 'duplicate'));
                    }}
                  >
                    <Copy size={15} /> Duplicate conversation
                  </button>
                  <button
                    role="menuitem"
                    disabled={!!menu.session.active_run_id || !menu.session.messages.length}
                    onClick={() => openDialog({ kind: 'fork', session: menu.session })}
                  >
                    <GitBranch size={15} /> Fork conversation
                  </button>
                  <button
                    role="menuitem"
                    onClick={() =>
                      openDialog(
                        { kind: 'move', session: menu.session },
                        menu.session.folder_id || '',
                      )
                    }
                  >
                    <FolderInput size={15} /> Move to folder
                  </button>
                  <button
                    role="menuitem"
                    disabled={!!menu.session.active_run_id}
                    onClick={() =>
                      openDialog(
                        { kind: 'project', session: menu.session },
                        menu.session.project_id || '',
                      )
                    }
                  >
                    <FolderInput size={15} /> Change workspace
                  </button>
                  <button
                    role="menuitem"
                    disabled={!!menu.session.active_run_id}
                    onClick={() => {
                      const source = menu.session;
                      setMenu(null);
                      void onAction(async () => {
                        await update(source, { archived: !source.archived });
                        await onUpdated();
                        if (source.archived) setArchived(false);
                      });
                    }}
                  >
                    {menu.session.archived ? <ArchiveRestore size={15} /> : <Archive size={15} />}
                    {menu.session.archived ? 'Restore conversation' : 'Archive conversation'}
                  </button>
                  <div className="menu-divider" />
                  <button
                    role="menuitem"
                    className="danger-text"
                    disabled={!!menu.session.active_run_id}
                    onClick={() => openDialog({ kind: 'delete', session: menu.session })}
                  >
                    <Trash2 size={15} /> Delete conversation
                  </button>
                </>
              ) : (
                <>
                  <button
                    role="menuitem"
                    autoFocus
                    onClick={() =>
                      openDialog({ kind: 'rename-folder', folder: menu.folder }, menu.folder.name)
                    }
                  >
                    <Pencil size={15} /> Rename folder
                  </button>
                  <button
                    role="menuitem"
                    className="danger-text"
                    onClick={() => openDialog({ kind: 'delete-folder', folder: menu.folder })}
                  >
                    <Trash2 size={15} /> Delete folder
                  </button>
                </>
              )}
            </div>
          </div>,
          document.body,
        )}
      {dialog &&
        createPortal(
          <div className="modal-backdrop">
            <form
              ref={dialogRef}
              tabIndex={-1}
              className="history-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="history-dialog-title"
              onSubmit={(event) => {
                event.preventDefault();
                void onAction(submit);
              }}
            >
              <div className="row-between">
                <h2 id="history-dialog-title">{title}</h2>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Close dialog"
                  disabled={busy}
                  onClick={() => setDialog(null)}
                >
                  <X size={16} />
                </button>
              </div>
              {needsName && (
                <label>
                  {dialog.kind === 'rename' ? 'Conversation name' : 'Folder name'}
                  <input
                    autoFocus
                    required
                    maxLength={120}
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                    disabled={busy}
                  />
                </label>
              )}
              {dialog.kind === 'move' && (
                <label>
                  Folder
                  <select
                    aria-label="Folder"
                    autoFocus
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                    disabled={busy}
                  >
                    <option value="">Unfiled</option>
                    {folders.map((folder) => (
                      <option key={folder.id} value={folder.id}>
                        {folder.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {dialog.kind === 'project' && (
                <>
                  <label>
                    Project
                    <select
                      aria-label="Conversation project"
                      autoFocus
                      value={value}
                      onChange={(event) => setValue(event.target.value)}
                      disabled={busy}
                    >
                      <option value="">Default workspace</option>
                      {projects.map((project) => (
                        <option key={project.id} value={project.id}>
                          {project.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="subtle">
                    Future Runs use this project's folder. Saved Runs retain their original
                    workspace and evidence.
                  </p>
                </>
              )}
              {dialog.kind === 'fork' && (
                <>
                  <p className="subtle">
                    Start an independent conversation through a completed turn. Later turns are
                    excluded; saved tool calls are not executed again. Workspace files are shared.
                  </p>
                  <label>
                    Fork after turn
                    <select
                      aria-label="Fork after turn"
                      autoFocus
                      value={value || forkTurns.at(-1)?.id || ''}
                      onChange={(event) => setValue(event.target.value)}
                      disabled={busy || turns.isPending}
                    >
                      {!forkTurns.length && (
                        <option value="">
                          {turns.isPending ? 'Loading turns…' : 'No completed turns'}
                        </option>
                      )}
                      {forkTurns.map((run) => (
                        <option key={run.id} value={run.id}>
                          #{(turns.data?.findIndex((item) => item.id === run.id) ?? -1) + 1} ·{' '}
                          {run.input.slice(0, 70)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {turns.isError && (
                    <p className="error-text" role="alert">
                      {turns.error.message}
                    </p>
                  )}
                </>
              )}
              {dialog.kind === 'delete' && (
                <p>
                  Delete “{dialog.session.title}” and its saved runs and traces? This cannot be
                  undone.
                </p>
              )}
              {dialog.kind === 'delete-folder' && (
                <p>Delete “{dialog.folder.name}”? Its conversations will move to Unfiled.</p>
              )}
              {error && (
                <p className="error-text" role="alert">
                  {error}
                </p>
              )}
              <div className="dialog-actions">
                <button
                  type="button"
                  className="button"
                  disabled={busy}
                  onClick={() => setDialog(null)}
                >
                  Cancel
                </button>
                <button
                  className={`button ${destructive ? 'danger' : 'primary'}`}
                  disabled={
                    busy ||
                    (needsName && !value.trim()) ||
                    (dialog.kind === 'fork' && !forkTurns.length)
                  }
                >
                  {destructive
                    ? 'Delete'
                    : dialog.kind === 'fork'
                      ? 'Create fork'
                      : dialog.kind === 'move'
                        ? 'Move'
                        : 'Save'}
                </button>
              </div>
            </form>
          </div>,
          document.body,
        )}
    </>
  );
}
