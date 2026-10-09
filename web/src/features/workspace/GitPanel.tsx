import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { GitCompare, RefreshCw, SearchCheck, X } from 'lucide-react';
import { errorMessage, request, workspacePath } from '../../api/client';
import type { components } from '../../api/generated';
import { Diff } from '../../shared/Diff';
import { reviewPrompt } from './review';

type GitStatus = components['schemas']['GitStatus'];
type GitDiff = components['schemas']['GitDiff'];

/** Two-letter Git state: index then working tree, as in `git status --short`. */
function code(file: GitStatus['files'][number]): string {
  return file.kind === 'untracked' ? '??' : `${file.index}${file.worktree}`.replaceAll('.', ' ');
}

/** The workspace's uncommitted changes, read by Glassbox (the agent cannot run Git). */
export function GitPanel({
  projectId,
  onClose,
  onReview,
}: {
  projectId: string;
  onClose: () => void;
  onReview: (task: string) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [preparing, setPreparing] = useState(false);
  const status = useQuery({
    queryKey: ['git', projectId],
    queryFn: () => request<GitStatus>(workspacePath('/git', projectId)),
  });
  const file = status.data?.files.find((item) => item.path === selected);
  const diff = useQuery({
    queryKey: ['git-diff', projectId, selected],
    queryFn: () =>
      request<GitDiff>(workspacePath(`/git/diff?path=${encodeURIComponent(selected!)}`, projectId)),
    enabled: !!file && !file.private,
  });
  const reviewable = status.data?.files.some((item) => !item.private) ?? false;

  async function review() {
    setPreparing(true);
    setError('');
    try {
      onReview(reviewPrompt(await request<GitDiff>(workspacePath('/git/diff', projectId))));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setPreparing(false);
    }
  }

  const data = status.data;
  return (
    <aside className="panel workspace-panel" aria-label="Git changes">
      <div className="panel-heading">
        <GitCompare size={16} />
        <h2>Changes</h2>
        <button
          className="icon-button"
          aria-label="Refresh changes"
          title="Refresh"
          onClick={() => {
            void status.refetch();
            if (selected) void diff.refetch();
          }}
        >
          <RefreshCw size={15} />
        </button>
        <button className="icon-button" aria-label="Close changes" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className="workspace-content">
        {status.isPending && <p className="subtle">Reading Git status…</p>}
        {(error || status.isError) && (
          <p className="error-banner" role="alert">
            {error || errorMessage(status.error)}
          </p>
        )}
        {data && !data.repository && (
          <p className="subtle">
            This workspace is not in a Git repository{data.reason ? `: ${data.reason}` : '.'}
          </p>
        )}
        {data?.repository && (
          <>
            <p className="git-branch">
              <strong>{data.branch ?? 'Detached HEAD'}</strong>
              {data.upstream && (
                <span className="subtle">
                  {' '}
                  · {data.upstream}
                  {data.ahead > 0 && ` ↑${data.ahead}`}
                  {data.behind > 0 && ` ↓${data.behind}`}
                </span>
              )}
            </p>
            {data.files.length === 0 ? (
              <p className="subtle">No uncommitted changes.</p>
            ) : (
              <ul className="git-files" aria-label="Changed files">
                {data.files.map((item) => (
                  <li key={item.path}>
                    <button
                      aria-pressed={item.path === selected}
                      onClick={() => setSelected(item.path === selected ? null : item.path)}
                    >
                      <code className="git-code">{code(item)}</code>
                      <span className="git-path">
                        {item.original_path ? `${item.original_path} → ` : ''}
                        {item.path}
                      </span>
                      {item.private && <span className="badge">private</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {data.truncated && (
              <p className="subtle">Only the first {data.files.length} changes are listed.</p>
            )}
            <div className="row">
              <button
                className="button primary"
                disabled={!reviewable || preparing}
                onClick={() => void review()}
              >
                <SearchCheck size={14} /> Review changes
              </button>
            </div>
            <p className="subtle">
              Review changes puts the diff into a code-review task in the message box for you to
              send. Git runs on this computer with repository programs (hooks, fsmonitor, diff
              drivers) disabled; private files such as .env are listed but never shown.
            </p>
            {file?.private && <p className="subtle">The content of private files is not shown.</p>}
            {diff.isError && <p role="alert">{errorMessage(diff.error)}</p>}
            {file && diff.data && (
              <section aria-label={`Diff of ${file.path}`}>
                {diff.data.diff ? (
                  <Diff text={diff.data.diff} />
                ) : (
                  <p className="subtle">No text changes to show.</p>
                )}
                {diff.data.truncated && <p className="subtle">The diff was cut short.</p>}
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
