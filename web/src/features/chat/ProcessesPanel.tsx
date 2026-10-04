import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Terminal, X } from 'lucide-react';
import { request, errorMessage, type Session } from '../../api/client';
import type { components } from '../../api/generated';

type Process = components['schemas']['ProcessView'];
const active = new Set(['starting', 'running', 'stopping']);

/** Inspect owned commands and send explicit stdin or stop actions without replaying them. */
export function ProcessesPanel({ session, onClose }: { session: Session; onClose: () => void }) {
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const query = useQuery({
    queryKey: ['processes', session.id],
    queryFn: () => request<Process[]>(`/sessions/${session.id}/processes`),
    refetchInterval: (state) =>
      session.active_run_id || state.state.data?.some((item) => active.has(item.status))
        ? 1000
        : false,
  });
  /** Send one user action and reload its durable lifecycle projection. */
  async function act(item: Process, operation: string, body?: unknown) {
    setBusy(item.id);
    setError('');
    try {
      await request(`/sessions/${session.id}/processes/${item.id}/${operation}`, 'POST', body);
      if (operation === 'input') setInputs((previous) => ({ ...previous, [item.id]: '' }));
      await query.refetch();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy('');
    }
  }
  return (
    <section className="panel workspace-panel" aria-label="Conversation processes">
      <header className="panel-heading">
        <Terminal size={17} />
        <h2>Processes</h2>
        <button className="icon-button" aria-label="Close processes" onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      <div className="workspace-content">
        {(error || query.isError) && (
          <p className="notice warning" role="alert">
            {error || errorMessage(query.error)}
          </p>
        )}
        <p className="subtle">
          Native sandbox · pipe input · no command networking. Windows file changes appear after
          exit.
        </p>
        {!query.data?.length && (
          <p className="subtle">
            No background commands in this conversation. Ask the agent to use start_process.
          </p>
        )}
        {[...(query.data || [])].reverse().map((item) => (
          <article className="process-card" key={item.id}>
            <div className="row">
              <strong>{item.status.replaceAll('_', ' ')}</strong>
              <span className="subtle">
                PID {item.pid ?? 'pending'} · {item.mode}
              </span>
            </div>
            <code className="process-command">{item.command}</code>
            <p className="subtle">
              {item.cwd} · {item.id.slice(0, 10)}
            </p>
            <pre className="process-output">
              {item.stdout || '(No stdout yet)'}
              {item.stderr && `\n[stderr]\n${item.stderr}`}
            </pre>
            {item.truncated && <p className="notice">Output was truncated.</p>}
            {item.error && <p className="notice warning">{item.error}</p>}
            {active.has(item.status) ? (
              <>
                <label>
                  Pipe input
                  <textarea
                    value={inputs[item.id] || ''}
                    maxLength={8192}
                    disabled={busy === item.id || item.stdin_closed}
                    onChange={(event) => setInputs({ ...inputs, [item.id]: event.target.value })}
                  />
                </label>
                <div className="row">
                  <button
                    className="button"
                    disabled={busy === item.id || item.stdin_closed || !inputs[item.id]}
                    onClick={() => void act(item, 'input', { text: inputs[item.id] + '\n' })}
                  >
                    Send line
                  </button>
                  <button
                    className="button quiet"
                    disabled={busy === item.id || item.stdin_closed}
                    onClick={() => void act(item, 'input', { eof: true })}
                  >
                    Close stdin
                  </button>
                  <button
                    className="button danger"
                    disabled={busy === item.id}
                    onClick={() => void act(item, 'stop')}
                  >
                    Stop process
                  </button>
                </div>
              </>
            ) : (
              item.result && (
                <details>
                  <summary>Exit result</summary>
                  <pre>{JSON.stringify(item.result, null, 2)}</pre>
                </details>
              )
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
