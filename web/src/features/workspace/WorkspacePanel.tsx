import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus, RefreshCw, X } from 'lucide-react';
import { PackageActions } from '../../shared/PackageActions';
import {
  request,
  workspacePath,
  errorMessage,
  type WorkspaceView,
  type SkillDocument,
  type MemoryDocument,
} from '../../api/client';

/** Inspect the real sandbox and edit ordinary workspace files without a separate settings system. */
export function WorkspacePanel({
  projectId,
  selected,
  onSelect,
  onClose,
  onChanged,
}: {
  projectId: string;
  selected: string[];
  onSelect: (names: string[]) => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  const query = useQuery({
    queryKey: ['workspace', projectId],
    queryFn: () => request<WorkspaceView>(workspacePath('', projectId)),
    refetchOnWindowFocus: false,
  });
  const [tab, setTab] = useState<'memory' | 'skills'>('memory');
  const [skill, setSkill] = useState<SkillDocument | null | undefined>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const changed = async (message: string) => {
    onChanged();
    await query.refetch();
    // Hash-keyed editors remount after a save, so keep success feedback in their parent.
    setNotice(message);
  };
  return (
    <aside className="panel workspace-panel" aria-label="Workspace files">
      <div className="panel-heading">
        <h2>Workspace</h2>
        <button
          className="icon-button"
          aria-label="Refresh workspace"
          onClick={() => void query.refetch()}
        >
          <RefreshCw size={15} />
        </button>
        <button className="icon-button" aria-label="Close workspace" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      {query.isPending && <p className="subtle">Checking workspace…</p>}
      {(query.isError || error) && (
        <p role="alert" className="error-text">
          {error || query.error?.message}
        </p>
      )}
      {query.data && (
        <div className="workspace-content">
          <div className="workspace-path">{query.data.root}</div>
          <details className="sandbox-status">
            <summary>
              Sandbox · {query.data.sandbox.ready ? query.data.sandbox.backend : 'unavailable'} ·
              network off
            </summary>
            <p className="subtle">{query.data.sandbox.detail}</p>
            <button
              className="text-button"
              onClick={() =>
                void request(workspacePath('?refresh=true', projectId))
                  .then(() => query.refetch())
                  .catch((caught) => setError(errorMessage(caught)))
              }
            >
              Recheck sandbox
            </button>
          </details>
          <div className="row workspace-tabs">
            <button
              className="button quiet"
              aria-pressed={tab === 'memory'}
              onClick={() => setTab('memory')}
            >
              Memory
            </button>
            <button
              className="button quiet"
              aria-pressed={tab === 'skills'}
              onClick={() => setTab('skills')}
            >
              Skills
            </button>
          </div>
          {tab === 'memory' ? (
            <MemoryEditor
              projectId={projectId}
              key={query.data.memory.hash}
              memory={query.data.memory}
              onSaved={() => changed('Memory saved. New runs will use it.')}
            />
          ) : (
            <>
              <p className="subtle">
                Select skills for the next run, or mention $skill-name in your message.
              </p>
              <PackageActions
                kind="skill"
                filename={`${skill?.name || 'workspace'}.skill.json`}
                exportPackage={
                  skill
                    ? () => request(workspacePath(`/skills/${skill.name}/export`, projectId))
                    : undefined
                }
                importPackage={async (value) => {
                  setSkill(
                    await request<SkillDocument>(
                      workspacePath('/skills/import', projectId),
                      'POST',
                      value,
                    ),
                  );
                  await changed('Skill package imported.');
                }}
              />
              {query.data.skills.map((item) => (
                <div className="skill-row" key={item.name}>
                  <input
                    type="checkbox"
                    aria-label={`Use ${item.name}`}
                    checked={selected.includes(item.name)}
                    onChange={(event) =>
                      onSelect(
                        event.target.checked
                          ? [...selected, item.name]
                          : selected.filter((name) => name !== item.name),
                      )
                    }
                  />
                  <button
                    className="text-button"
                    title={item.description}
                    onClick={() =>
                      void request<SkillDocument>(workspacePath(`/skills/${item.name}`, projectId))
                        .then(setSkill)
                        .catch((caught) => setError(errorMessage(caught)))
                    }
                  >
                    {item.name}
                    {item.builtin ? ' · built-in' : ''}
                  </button>
                </div>
              ))}
              {Object.entries(query.data.skill_errors).map(([name, message]) => (
                <p key={name} className="error-text">
                  {name}: {message}
                </p>
              ))}
              <button className="button" onClick={() => setSkill(null)}>
                <Plus size={14} /> New skill
              </button>
              {skill !== undefined && (
                <SkillEditor
                  key={skill?.hash || 'new'}
                  projectId={projectId}
                  skill={skill}
                  onSaved={async (saved) => {
                    setSkill(saved);
                    await changed('Skill saved. New runs will discover it.');
                  }}
                />
              )}
            </>
          )}
          {notice && (
            <p role="status" className="subtle">
              {notice}
            </p>
          )}
        </div>
      )}
    </aside>
  );
}

/** Use the last-read hash so saving notes cannot silently erase another run's update. */
function MemoryEditor({
  projectId,
  memory,
  onSaved,
}: {
  projectId: string;
  memory: MemoryDocument;
  onSaved: () => Promise<void>;
}) {
  const [content, setContent] = useState(memory.content);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const save = async () => {
    setBusy(true);
    setNotice('');
    try {
      await request(workspacePath('/memory', projectId), 'PUT', {
        content,
        expected_hash: memory.hash,
      });
      await onSaved();
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="workspace-editor">
      <label>
        Project memory
        <textarea
          aria-label="Project memory"
          rows={14}
          value={content}
          onChange={(event) => setContent(event.target.value)}
        />
      </label>
      <p className="subtle">{memory.path} · Loaded with AGENTS.md at the start of each run.</p>
      <button
        className="button"
        disabled={busy || content === memory.content}
        onClick={() => void save()}
      >
        Save memory
      </button>
      {notice && (
        <p role="status" className="subtle">
          {notice}
        </p>
      )}
    </div>
  );
}

/** Save only the skill fields the file format needs; bundled instructions remain inspectable. */
function SkillEditor({
  projectId,
  skill,
  onSaved,
}: {
  projectId: string;
  skill: SkillDocument | null;
  onSaved: (saved: SkillDocument) => Promise<void>;
}) {
  const [name, setName] = useState(skill?.name || '');
  const [description, setDescription] = useState(skill?.description || '');
  const [instructions, setInstructions] = useState(
    skill?.content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').trim() || '',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const saved = await request<SkillDocument>(workspacePath('/skills', projectId), 'PUT', {
        name,
        description,
        instructions,
        expected_hash: skill?.hash,
      });
      await onSaved(saved);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  return (
    <fieldset className="workspace-editor" disabled={busy || !!skill?.builtin}>
      <label>
        Skill name
        <input
          value={name}
          disabled={!!skill}
          onChange={(event) => setName(event.target.value)}
          placeholder="review-python"
        />
      </label>
      <label>
        Description
        <textarea
          rows={2}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <label>
        Instructions
        <textarea
          rows={12}
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
        />
      </label>
      {skill && <p className="subtle">{skill.path}</p>}
      {!skill?.builtin && (
        <button
          className="button"
          disabled={!name || !description || !instructions}
          onClick={() => void save()}
        >
          Save skill
        </button>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
    </fieldset>
  );
}
