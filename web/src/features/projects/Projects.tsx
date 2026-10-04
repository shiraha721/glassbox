import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { FolderOpen, Plus, X } from 'lucide-react';
import { request, type Project } from '../../api/client';
import { DirectoryField } from './DirectoryField';
import { useDialogFocus } from '../../shared/useDialogFocus';

/** Select named local folders and register projects without copying their workspace contents. */
export function Projects({
  projects,
  selected,
  defaultRoot,
  busy,
  error,
  onSelect,
  onAction,
  onUpdated,
}: {
  projects: Project[];
  selected: string;
  defaultRoot: string;
  busy: boolean;
  error: string;
  onSelect: (id: string) => void;
  onAction: (action: () => Promise<void>) => Promise<void>;
  onUpdated: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [root, setRoot] = useState('');
  const [picking, setPicking] = useState(false);
  const dialogRef = useRef<HTMLFormElement>(null);
  useDialogFocus(dialogRef, open, () => {
    if (!busy && !picking) setOpen(false);
  });
  const submit = async () => {
    const created = await request<Project>('/projects', 'POST', {
      name: name.trim(),
      workspace_root: root.trim(),
    });
    await onUpdated();
    onSelect(created.id);
    setOpen(false);
  };
  return (
    <>
      <section className="projects" aria-label="Projects">
        <div className="row-between">
          <span className="subtle">Projects</span>
          <button
            className="icon-button"
            aria-label="Add project"
            title="Add project"
            disabled={busy}
            onClick={() => {
              setName('');
              setRoot('');
              setOpen(true);
            }}
          >
            <Plus size={15} />
          </button>
        </div>
        <div className="project-list">
          <button
            className="project-item"
            aria-pressed={!selected}
            title={defaultRoot}
            disabled={busy}
            onClick={() => onSelect('')}
          >
            <FolderOpen size={14} />
            <span>Default workspace</span>
          </button>
          {projects.map((project) => (
            <button
              key={project.id}
              className="project-item"
              aria-pressed={selected === project.id}
              title={project.workspace_root}
              disabled={busy}
              onClick={() => onSelect(project.id)}
            >
              <FolderOpen size={14} />
              <span>{project.name}</span>
            </button>
          ))}
        </div>
      </section>
      {open &&
        createPortal(
          <div className="modal-backdrop">
            <form
              ref={dialogRef}
              tabIndex={-1}
              className="history-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="project-title"
              onSubmit={(event) => {
                event.preventDefault();
                if (!busy && !picking) void onAction(submit);
              }}
            >
              <div className="row-between">
                <h2 id="project-title">Add project</h2>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Close project dialog"
                  disabled={busy || picking}
                  onClick={() => setOpen(false)}
                >
                  <X size={16} />
                </button>
              </div>
              <label>
                Project name
                <input
                  required
                  maxLength={120}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  disabled={busy}
                />
              </label>
              <DirectoryField
                value={root}
                initialPath={defaultRoot}
                disabled={busy}
                onBusyChange={setPicking}
                onChange={(path) => {
                  setRoot(path);
                  if (!name.trim())
                    setName(
                      path
                        .replace(/[\\/]+$/, '')
                        .split(/[\\/]/)
                        .at(-1) || 'Project',
                    );
                }}
              />
              <p className="subtle">
                Conversations use this folder's files, instructions, memory and Skills.
              </p>
              {error && (
                <p role="alert" className="error-text">
                  {error}
                </p>
              )}
              <div className="dialog-actions">
                <button
                  type="button"
                  className="button"
                  disabled={busy || picking}
                  onClick={() => setOpen(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="button primary"
                  disabled={busy || picking || !name.trim() || !root.trim()}
                >
                  Add project
                </button>
              </div>
            </form>
          </div>,
          document.body,
        )}
    </>
  );
}
