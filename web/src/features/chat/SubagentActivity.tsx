import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { errorMessage, request, terminal, type Run } from '../../api/client';
import { StatusBadge } from '../../shared/StatusBadge';

/** The child Run behind a delegate call, followed while it works. */
export function SubagentActivity({
  runId,
  children,
}: {
  runId: string;
  children: (run: Run) => ReactNode;
}) {
  const query = useQuery({
    queryKey: ['subagent', runId],
    queryFn: () => request<Run>(`/runs/${runId}`),
    refetchInterval: (state) =>
      state.state.data && terminal(state.state.data.status) ? false : 2000,
  });
  if (query.isError)
    return (
      <p className="subtle" role="alert">
        {errorMessage(query.error)}
      </p>
    );
  if (!query.data) return <p className="subtle">Loading the sub-agent…</p>;
  return (
    <section className="subagent" aria-label="Sub-agent activity">
      <div className="row">
        <span className="section-label">Sub-agent</span>
        <StatusBadge status={query.data.status} />
      </div>
      {children(query.data)}
      {query.data.error && <p className="subtle">{query.data.error}</p>}
    </section>
  );
}
