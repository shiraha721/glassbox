import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { RunEvent } from './client';

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

/** Subscribe to persisted events; reconnecting reads state and never submits execution commands. */
export function useRunEvents(runId: string | undefined, enabled: boolean): void {
  const cache = useQueryClient();
  useEffect(() => {
    if (!runId || !enabled) return;
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const source = new EventSource(`/api/v1/runs/${runId}/events`);
    const refresh = () => {
      timer = undefined;
      void cache.invalidateQueries({ queryKey: ['runs'] });
      void cache.invalidateQueries({ queryKey: ['bootstrap'] });
      void cache.invalidateQueries({ queryKey: ['trace', runId] });
      void cache.invalidateQueries({ queryKey: ['processes'] });
      void cache.invalidateQueries({ queryKey: ['context'] });
    };
    const receive = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent;
      if (event.seq <= cursor) return;
      cursor = event.seq;
      // Coalesce token events into state reads instead of racing one request per token.
      timer ??= setTimeout(refresh, 80);
      if (['run.completed', 'run.failed', 'run.cancelled'].includes(event.type)) {
        if (timer) clearTimeout(timer);
        refresh();
        // Tool writes can change memory and the skill catalog for the next turn.
        void cache.invalidateQueries({ queryKey: ['workspace'] });
        source.close();
      }
    };
    eventTypes.forEach((type) => source.addEventListener(type, receive as EventListener));
    return () => {
      source.close();
      if (timer) clearTimeout(timer);
      // A status read can finish the Run before its last SSE event arrives. Flush the final
      // projection refresh on unsubscribe so active_run_id/revision cannot remain stale.
      refresh();
    };
  }, [runId, enabled, cache]);
}
