import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus, Trash2 } from 'lucide-react';
import { errorMessage, request, type SkillDocument, type WorkspaceSkills } from '../../api/client';
import { SkillEditor } from '../workspace/SkillEditor';

/** Manage global skills, which every project can use, beside the read-only bundled ones. */
export function SkillsPage() {
  const query = useQuery({
    queryKey: ['global-skills'],
    queryFn: () => request<WorkspaceSkills>('/skills'),
  });
  const [skill, setSkill] = useState<SkillDocument | null | undefined>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const open = async (name: string) => {
    setError('');
    setNotice('');
    try {
      setSkill(await request<SkillDocument>(`/skills/${encodeURIComponent(name)}`));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  };
  const remove = async (target: SkillDocument) => {
    if (!window.confirm(`Delete the global skill ${target.name}?`)) return;
    try {
      await request(
        `/skills/${encodeURIComponent(target.name)}?expected_hash=${encodeURIComponent(target.hash)}`,
        'DELETE',
      );
      setSkill(undefined);
      setNotice(`Deleted ${target.name}.`);
      await query.refetch();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  };
  return (
    <>
      <p className="page-intro">
        Global skills are available in every project. Project skills live in each project&apos;s
        .agents/skills folder and are managed in the Workspace panel. The agent sees each
        skill&apos;s name and description and loads the full text when it is relevant.
      </p>
      <div className="row">
        <button
          className="button"
          onClick={() => {
            setNotice('');
            setSkill(null);
          }}
        >
          <Plus size={14} /> New skill
        </button>
      </div>
      {query.isError && (
        <p className="error-text" role="alert">
          {errorMessage(query.error)}
        </p>
      )}
      <div className="skill-list">
        {query.data?.skills.map((item) => (
          <button
            key={item.name}
            className="skill-list-item"
            aria-pressed={skill?.name === item.name}
            onClick={() => void open(item.name)}
          >
            <span>
              <strong>{item.name}</strong>
              <small>{item.description}</small>
            </span>
            <span className="badge">{item.scope === 'builtin' ? 'bundled' : 'global'}</span>
          </button>
        ))}
      </div>
      {Object.entries(query.data?.errors || {}).map(([name, message]) => (
        <p key={name} className="error-text">
          {name}: {message}
        </p>
      ))}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="success-text" role="status">
          {notice}
        </p>
      )}
      {skill !== undefined && (
        <section className="settings-card">
          <div className="row-between">
            <h3>{skill ? skill.name : 'New global skill'}</h3>
            {skill?.scope === 'global' && (
              <button className="button small danger" onClick={() => void remove(skill)}>
                <Trash2 size={13} /> Delete
              </button>
            )}
          </div>
          {skill?.scope === 'builtin' && (
            <p className="subtle">
              Bundled skills are read-only. Export or copy one to customize it.
            </p>
          )}
          <SkillEditor
            key={skill?.hash || 'new'}
            savePath="/skills"
            editable={!skill || skill.scope === 'global'}
            skill={skill}
            onSaved={async (saved) => {
              setSkill(saved);
              setNotice('Skill saved. New runs in every project will discover it.');
              await query.refetch();
            }}
          />
        </section>
      )}
    </>
  );
}
