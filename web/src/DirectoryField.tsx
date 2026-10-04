import { useEffect, useRef, useState } from 'react';
import { FolderOpen } from 'lucide-react';
import { ApiError, errorMessage, request, type FolderSelection } from './api/client';

/** Select a real server-side folder through the desktop, with manual entry only on unavailability. */
export function DirectoryField({
  value,
  initialPath,
  disabled,
  onChange,
  onBusyChange,
}: {
  value: string;
  initialPath: string;
  disabled: boolean;
  onChange: (path: string) => void;
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
      </div>
      {notice && (
        <p className="subtle" role="status">
          {notice}
        </p>
      )}
    </div>
  );
}
