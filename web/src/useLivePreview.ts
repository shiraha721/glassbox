import { useEffect, useState } from 'react';
import { errorMessage, request, type PromptInspection } from './api/client';

/** Debounce draft compilation, retaining the last result and rejecting late responses. */
export function useLivePreview(enabled: boolean, body: string, contextVersion: number) {
  const [result, setResult] = useState<{
    body: string;
    version: number;
    value: PromptInspection;
  }>();
  const [failure, setFailure] = useState<{ body: string; version: number; message: string }>();
  const current = result?.body === body && result.version === contextVersion;
  const error = failure?.body === body && failure.version === contextVersion ? failure.message : '';

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const parsed = JSON.parse(body);
          const variables: unknown = JSON.parse(parsed.variables);
          if (!variables || typeof variables !== 'object' || Array.isArray(variables))
            throw new Error('Variables must be a JSON object.');
          const value = await request<PromptInspection>(
            '/prompts/inspect',
            'POST',
            { ...parsed, variables },
            undefined,
            controller.signal,
          );
          // An input change aborts the fetch and invalidates its closure before it can publish.
          if (!cancelled) {
            setResult({ body, version: contextVersion, value });
            setFailure(undefined);
          }
        } catch (caught) {
          if (!cancelled)
            setFailure({ body, version: contextVersion, message: errorMessage(caught) });
        }
      })();
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [enabled, body, contextVersion]);

  return {
    preview: enabled ? result?.value || null : null,
    updating: enabled && !current && !error,
    error: enabled ? error : '',
  };
}
