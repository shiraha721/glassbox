import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { request, type Run, type RunEvent } from './client';

const eventTypes = [
  'run.queued',
  'run.started',
  'run.cancelling',
  'run.cancelled',
  'run.completed',
  'run.failed',
  'run.interrupted',
  'run.metrics',
  'model.started',
  'model.delta',
  'model.completed',
  'tool.started',
  'tool.output',
  'tool.completed',
  'approval.required',
  'approval.resolved',
];
// Events after which saved evidence, conversation metadata or workspace files may have changed.
const boundaries = new Set([
  'model.completed',
  'tool.completed',
  'approval.required',
  'approval.resolved',
  'run.interrupted',
]);
const terminalEvents = new Set(['run.completed', 'run.failed', 'run.cancelled']);

/** Subscribe to persisted events; reconnecting reads state and never submits execution commands.
 *
 * Token and output events refresh only the active Run's projection, coalesced into at most one
 * request in flight. Broader queries refresh at lifecycle boundaries and when the stream ends. */
export function useRunEvents(run: Pick<Run, 'id' | 'session_id'> | undefined): void {
  const cache = useQueryClient();
  const runId = run?.id;
  const sessionId = run?.session_id;
  useEffect(() => {
    if (!runId || !sessionId) return;
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    let again = false;
    const source = new EventSource(`/api/v1/runs/${runId}/events`);
    const refreshRun = async () => {
      timer = undefined;
      if (inFlight) {
        again = true;
        return;
      }
      inFlight = true;
      try {
        const fresh = await request<Run>(`/runs/${runId}`);
        cache.setQueryData<Run[]>(['runs', sessionId], (current) =>
          current?.map((item) => (item.id === fresh.id ? fresh : item)),
        );
      } catch {
        // The fallback list poll and the next event retry; a missed refresh is not an error.
      } finally {
        inFlight = false;
        if (again) {
          again = false;
          timer ??= setTimeout(() => void refreshRun(), 100);
        }
      }
    };
    const refreshAll = () => {
      void cache.invalidateQueries({ queryKey: ['runs', sessionId] });
      void cache.invalidateQueries({ queryKey: ['bootstrap'] });
      void cache.invalidateQueries({ queryKey: ['trace', runId] });
      void cache.invalidateQueries({ queryKey: ['processes', sessionId] });
      void cache.invalidateQueries({ queryKey: ['context', sessionId] });
      // Tool writes can change memory and the skill catalog for the next turn.
      void cache.invalidateQueries({ queryKey: ['workspace'] });
    };
    const receive = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent;
      if (event.seq <= cursor) return;
      cursor = event.seq;
      if (terminalEvents.has(event.type)) {
        if (timer) clearTimeout(timer);
        timer = undefined;
        source.close();
        refreshAll();
        return;
      }
      timer ??= setTimeout(() => void refreshRun(), 100);
      if (boundaries.has(event.type)) {
        void cache.invalidateQueries({ queryKey: ['trace', runId] });
        void cache.invalidateQueries({ queryKey: ['processes', sessionId] });
        if (event.type !== 'model.completed')
          void cache.invalidateQueries({ queryKey: ['bootstrap'] });
      }
    };
    eventTypes.forEach((type) => source.addEventListener(type, receive as EventListener));
    return () => {
      source.close();
      if (timer) clearTimeout(timer);
      // A status read can finish the Run before its last SSE event arrives. Refresh on
      // unsubscribe so active_run_id, revisions and workspace context cannot remain stale.
      refreshAll();
    };
  }, [runId, sessionId, cache]);
}
