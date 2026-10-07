import { useEffect, useRef, useState } from 'react';
import { FolderOpen } from 'lucide-react';
import { ApiError, errorMessage, request, type FolderSelection } from '../../api/client';

/** Select a server-side folder through the system dialog, or type a path that may be created.
 *
 * Manual entry opens on request or when no dialog is available. Only a typed path can name a
 * folder that does not exist yet; `create` asks the server to create it. */
export function DirectoryField({
  value,
  initialPath,
  disabled,
  create,
  onChange,
  onCreateChange,
  onBusyChange,
}: {
  value: string;
  initialPath: string;
  disabled: boolean;
  create: boolean;
  onChange: (path: string) => void;
  onCreateChange: (create: boolean) => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState(false);
  const [notice, setNotice] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  const browse = async () => {
    const pending = new AbortController();
    controller.current = pending;
    setBusy(true);
    onBusyChange(true);
    setNotice('Select a folder in the system dialog.');
    try {
      const result = await request<FolderSelection>(
        '/desktop/folder-picker',
        'POST',
        { initial_path: value || initialPath },
        undefined,
        pending.signal,
      );
      if (result.status === 'selected' && result.path) {
        onChange(result.path);
        setManual(false);
        onCreateChange(false);
        setNotice('');
      } else if (result.status === 'unavailable') {
        setManual(true);
        setNotice(result.detail || 'The system dialog is unavailable. Enter a folder path.');
      } else setNotice('Folder selection cancelled.');
    } catch (caught) {
      if (pending.signal.aborted) setNotice('Folder selection cancelled.');
      else if (caught instanceof ApiError && caught.code === 'folder_picker_busy') {
        setNotice(caught.message);
      } else {
        setManual(true);
        setNotice(errorMessage(caught) + ' Enter the folder path below.');
      }
    } finally {
      controller.current = null;
      setBusy(false);
      onBusyChange(false);
    }
  };

  return (
    <div className="directory-field">
      <label>
        Workspace folder
        {manual ? (
          <input
            autoFocus
            required
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={initialPath}
            disabled={disabled || busy}
          />
        ) : (
          <output className="directory-path" title={value}>
            {value || 'No folder selected'}
          </output>
        )}
      </label>
      <div className="row">
        <button
          className="button"
          type="button"
          disabled={disabled || busy}
          onClick={() => void browse()}
        >
          <FolderOpen size={15} /> {value ? 'Change folder' : 'Choose folder'}
        </button>
        {busy && (
          <button className="text-button" type="button" onClick={() => controller.current?.abort()}>
            Cancel selection
          </button>
        )}
        {!busy && !manual && (
          <button
            className="text-button"
            type="button"
            disabled={disabled}
            onClick={() => {
              setManual(true);
              setNotice('');
            }}
          >
            Enter a path
          </button>
        )}
      </div>
      {manual && (
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={create}
            disabled={disabled || busy}
            onChange={(event) => onCreateChange(event.target.checked)}
          />
          Create the folder if it does not exist
        </label>
      )}
      {notice && (
        <p className="subtle" role="status">
          {notice}
        </p>
      )}
    </div>
  );
}
