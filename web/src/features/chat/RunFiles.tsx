import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { FilePen, Undo2 } from 'lucide-react';
import { errorMessage, request, type Run } from '../../api/client';
import type { components } from '../../api/generated';

type FileChanges = components['schemas']['FileChanges'];

const stateLabels: Record<FileChanges['files'][number]['state'], string> = {
  as_left: '',
  reverted: 'restored',
  changed: 'changed since',
};

/** Files a finished Run changed with file tools, with undo for those still as it left them. */
export function RunFiles({ run, busy }: { run: Run; busy: boolean }) {
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [notice, setNotice] = useState('');
  const query = useQuery({
    queryKey: ['run-files', run.id],
    queryFn: () => request<FileChanges>(`/runs/${run.id}/files`),
    enabled: open,
  });
  const files = query.data?.files;
  const undoable = files?.some((item) => item.state === 'as_left') ?? false;
  const count = run.changed_files.length;

  async function undo() {
    setWorking(true);
    setNotice('');
    try {
      const result = await request<FileChanges>(`/runs/${run.id}/files/revert`, 'POST');
      cache.setQueryData(['run-files', run.id], result);
      void cache.invalidateQueries({ queryKey: ['workspace'] });
      const restored = `Restored ${result.restored.length} file${result.restored.length === 1 ? '' : 's'}`;
      setNotice(
        result.skipped.length
          ? `${restored}; left ${result.skipped.join(', ')} alone because it changed after this run.`
          : `${restored}.`,
      );
    } catch (failure) {
      setNotice(errorMessage(failure));
    } finally {
      setWorking(false);
    }
  }

  return (
    <details
      className="run-files"
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
    >
      <summary>
        <FilePen size={14} />
        Changed {count} file{count === 1 ? '' : 's'}
      </summary>
      {query.isError && <p role="alert">{errorMessage(query.error)}</p>}
      <ul aria-label="Changed files">
        {(files ?? run.changed_files.map((path) => ({ path, state: null }))).map((item) => (
          <li key={item.path}>
            <code>{item.path}</code>
            {'created' in item && item.created && <span className="badge">new</span>}
            {'deleted' in item && item.deleted && <span className="badge">deleted</span>}
            {item.state && stateLabels[item.state] && (
              <span className="subtle">{stateLabels[item.state]}</span>
            )}
          </li>
        ))}
      </ul>
      <div className="row">
        <button
          className="button small"
          disabled={busy || working || !undoable}
          onClick={() => void undo()}
        >
          <Undo2 size={13} /> Undo file changes
        </button>
      </div>
      {notice && (
        <p className="subtle" role="status">
          {notice}
        </p>
      )}
      <p className="subtle">
        Undo restores files still as this run left them. Changes made by shell commands are not
        tracked.
      </p>
    </details>
  );
}
