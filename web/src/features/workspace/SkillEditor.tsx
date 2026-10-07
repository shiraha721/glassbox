import { useState } from 'react';
import { errorMessage, request, type SkillDocument } from '../../api/client';

/** Save only the skill fields the file format needs; bundled instructions remain inspectable. */
export function SkillEditor({
  savePath,
  editable,
  skill,
  onSaved,
}: {
  savePath: string;
  editable: boolean;
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
      const saved = await request<SkillDocument>(savePath, 'PUT', {
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
    <fieldset className="workspace-editor" disabled={busy || !editable}>
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
      {editable && (
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
