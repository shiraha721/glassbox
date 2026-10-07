const tones: Record<string, string> = {
  succeeded: 'success',
  exited: 'success',
  completed: 'success',
  failed: 'danger',
  timed_out: 'danger',
  waiting_approval: 'warning',
  interrupted: 'warning',
  outcome_unknown: 'warning',
  queued: 'progress',
  running: 'progress',
  cancelling: 'progress',
  starting: 'progress',
  stopping: 'progress',
  executing: 'progress',
  pending: 'progress',
};

/** Show a lifecycle state as text with a color tone; the text alone carries the meaning. */
export function StatusBadge({ status, testId }: { status: string; testId?: string }) {
  return (
    <span className="status" data-tone={tones[status] || 'neutral'} data-testid={testId}>
      {status.replaceAll('_', ' ')}
    </span>
  );
}
