import { useRef, useState } from 'react';
import { Download, Upload } from 'lucide-react';
import { errorMessage } from './api/client';

/** Import/export bounded JSON data packages without loading code or runtime configuration. */
export function PackageActions({
  kind,
  filename,
  exportPackage,
  importPackage,
  disabled = false,
}: {
  kind: 'preset' | 'skill';
  filename: string;
  exportPackage?: () => Promise<unknown>;
  importPackage: (value: unknown) => Promise<void>;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  /** Download only the reusable package supplied by the current editor/workspace. */
  async function save() {
    setError('');
    setBusy(true);
    try {
      const value = await exportPackage?.();
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }),
      );
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }
  /** Read one selected data file and leave validation to the API. */
  async function load(file?: File) {
    if (!file) return;
    setError('');
    setBusy(true);
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error('Package JSON exceeds 2 MiB.');
      await importPackage(JSON.parse(await file.text()));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = '';
    }
  }
  return (
    <div className="package-actions">
      <div className="row">
        {exportPackage && (
          <button className="button small" disabled={disabled || busy} onClick={() => void save()}>
            <Download size={13} /> Export {kind}
          </button>
        )}
        <button
          className="button small"
          disabled={disabled || busy}
          onClick={() => input.current?.click()}
        >
          <Upload size={13} /> Import {kind}
        </button>
        <input
          ref={input}
          type="file"
          accept="application/json,.json"
          hidden
          aria-label={`Import ${kind} package`}
          onChange={(event) => void load(event.target.files?.[0])}
        />
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
