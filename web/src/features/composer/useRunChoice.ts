import { useEffect, useRef, useState } from 'react';
import { SIMULATED, type Bootstrap, type Run, type RunChoice } from '../../api/client';

const storageKey = 'studio.choice';

/** The saved default model and workspace policy, used until the user picks something else. */
function defaultChoice(config: Bootstrap['config']): RunChoice {
  const provider = config.provider;
  const providerId =
    provider.profile_id ||
    (provider.adapter === 'fake' ? SIMULATED : config.providers[0]?.id || SIMULATED);
  return {
    model: {
      provider_id: providerId,
      model: providerId === SIMULATED ? 'simulated' : provider.model,
      reasoning_effort: provider.reasoning_effort ?? null,
    },
    access: config.sandbox_mode === 'read-only' ? 'read-only' : 'workspace-write',
  };
}

/** Keep a choice only while its provider is still configured. */
function valid(choice: RunChoice | null, config: Bootstrap['config']): choice is RunChoice {
  return (
    !!choice?.model?.provider_id &&
    (choice.model.provider_id === SIMULATED ||
      config.providers.some((item) => item.id === choice.model.provider_id))
  );
}

/** Track the model and access for the next Run.
 *
 * Opening a conversation adopts the choice its latest Run used; otherwise the last choice
 * persists in this browser, falling back to the saved defaults. */
export function useRunChoice(
  data: Bootstrap,
  sessionId: string,
  runs: Run[],
  runsLoaded: boolean,
): [RunChoice, (choice: RunChoice) => void] {
  const [choice, setChoice] = useState<RunChoice>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(storageKey) || 'null') as RunChoice | null;
      if (valid(stored, data.config)) return stored;
    } catch {
      // A malformed preference falls back to the defaults.
    }
    return defaultChoice(data.config);
  });
  const adopted = useRef('');
  useEffect(() => {
    if (!sessionId || !runsLoaded || adopted.current === sessionId) return;
    adopted.current = sessionId;
    const last = runs.at(-1);
    if (!last?.provider_id || !last.model) return;
    const next: RunChoice = {
      model: {
        provider_id: last.provider_id,
        model: last.model,
        reasoning_effort: (last.reasoning_effort as RunChoice['model']['reasoning_effort']) ?? null,
      },
      access: (last.access as RunChoice['access']) || 'workspace-write',
    };
    // Adopting a finished conversation's settings is a render-time sync with loaded data.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (valid(next, data.config)) setChoice(next);
  }, [sessionId, runsLoaded, runs, data.config]);
  const update = (next: RunChoice) => {
    setChoice(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      // Storage can be unavailable; the choice still applies to this page.
    }
  };
  return [valid(choice, data.config) ? choice : defaultChoice(data.config), update];
}
