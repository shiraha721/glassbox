import { useState } from 'react';
import { KeyRound } from 'lucide-react';
import { errorMessage, request, type CredentialStatus } from '../../api/client';

/** Enter, replace or remove a named key; the value is sent once and never shown. */
export function KeyField({
  name,
  status,
  onChanged,
}: {
  name: string;
  status?: CredentialStatus;
  onChanged: () => Promise<unknown>;
}) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const act = async (method: 'PUT' | 'DELETE') => {
    setBusy(true);
    setError('');
    try {
      await request(
        `/credentials/${encodeURIComponent(name)}`,
        method,
        method === 'PUT' ? { value } : undefined,
      );
      setValue('');
      await onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  const description = !name
    ? 'No key: for local endpoints that do not need one.'
    : status?.source === 'stored'
      ? `Saved in Glassbox · ends in ${status.hint}`
      : status?.source === 'environment'
        ? `Read from the ${name} environment variable`
        : `Not set · enter a key or set ${name} in .env`;
  return (
    <div className="key-field">
      <label>
        API key
        <div className="row">
          <input
            type="password"
            autoComplete="off"
            aria-label="API key"
            placeholder={status?.source === 'stored' ? '••••••••' : 'Paste a key'}
            value={value}
            disabled={busy || !name}
            onChange={(event) => setValue(event.target.value)}
          />
          <button
            className="button"
            disabled={busy || !name || !value.trim()}
            onClick={() => void act('PUT')}
          >
            Save key
          </button>
        </div>
      </label>
      <div className="row-between">
        <span className={`key-status key-${status?.source || 'missing'}`}>
          <KeyRound size={13} />
          {description}
        </span>
        {status?.source === 'stored' && (
          <button className="text-button" disabled={busy} onClick={() => void act('DELETE')}>
            Remove saved key
          </button>
        )}
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
