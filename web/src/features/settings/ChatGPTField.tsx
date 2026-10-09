import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { errorMessage, request, type ChatGPTStatus } from '../../api/client';
import { usageUrl } from './providers';

/** How long to keep checking for a sign-in that continues in another tab. */
const WAIT_MS = 10 * 60 * 1000;

/** Sign a provider in with ChatGPT. The sign-in finishes in a browser tab ChatGPT returns to
 * on this server; the tokens stay on the server and this view only learns the outcome. */
export function ChatGPTField({ providerId }: { providerId: string }) {
  const [waiting, setWaiting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const status = useQuery({
    queryKey: ['chatgpt', providerId],
    queryFn: () => request<ChatGPTStatus>(`/chatgpt/${providerId}`),
    refetchInterval: waiting ? 2000 : false,
  });
  const ready = !!status.data?.plan_usage;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(() => setWaiting(false), WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [waiting]);
  useEffect(() => {
    // The tab ChatGPT returned to finished the sign-in.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (waiting && ready) setWaiting(false);
  }, [waiting, ready]);
  const signIn = async () => {
    setBusy(true);
    setNotice('');
    try {
      const { url } = await request<{ url: string }>(`/chatgpt/${providerId}/sign-in`, 'POST');
      window.open(url, '_blank', 'noopener');
      setWaiting(true);
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  const signOut = async () => {
    setBusy(true);
    setNotice('');
    try {
      const { revoked } = await request<{ revoked: boolean }>(`/chatgpt/${providerId}`, 'DELETE');
      if (!revoked)
        setNotice(
          'Signed out here, but ChatGPT did not confirm it. Disconnect Glassbox in ChatGPT’s settings as well.',
        );
      await status.refetch();
    } catch (caught) {
      setNotice(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };
  const data = status.data;
  return (
    <div className="key-field chatgpt-field">
      {data?.signed_in ? (
        <div className="row-between">
          <span className={`key-status ${ready ? 'key-stored' : 'key-missing'}`}>
            {ready
              ? `Using ChatGPT plan · signed in as ${data.email || 'your account'}`
              : `Signed in as ${data.email || 'your account'}, but using the plan was not allowed`}
          </span>
          <div className="row">
            {ready && (
              <a className="text-link" href={usageUrl} target="_blank" rel="noreferrer">
                Manage usage <ExternalLink size={12} />
              </a>
            )}
            <button className="text-button" disabled={busy} onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </div>
      ) : (
        <span className="key-status key-missing">
          {status.isLoading ? 'Checking sign-in…' : 'Not signed in'}
        </span>
      )}
      {!ready && (
        <div className="row">
          <button className="button primary" disabled={busy} onClick={() => void signIn()}>
            Continue with ChatGPT
          </button>
          {waiting && (
            <span className="subtle" role="status">
              Finish the sign-in in the new tab; this page updates when you are done.
            </span>
          )}
        </div>
      )}
      <p className="subtle">
        Requests from this provider use your ChatGPT plan and count toward its limits instead of API
        credit. OpenAI offers this to apps that run on your own computer; Glassbox gets no access to
        your ChatGPT conversations, and its sign-in stays on this computer.
      </p>
      {(notice || status.error) && (
        <p className="error-text" role="alert">
          {notice || errorMessage(status.error)}
        </p>
      )}
    </div>
  );
}
