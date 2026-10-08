import type { Run } from '../../api/client';
import type { components } from '../../api/generated';

export type Compaction = components['schemas']['CompactionView'];

/** Group compactions by the turn after which each belongs in the conversation.
 *
 * History holds each succeeded Run's task followed by its messages, in order, so a summary
 * covering N messages ends after the first succeeded Run whose running total reaches N.
 * One that cannot be placed (history changed since) goes after the latest turn. */
export function compactionsAfter(
  runs: Run[],
  compactions: Compaction[],
): Map<string, Compaction[]> {
  const boundaries: [number, string][] = [];
  let total = 0;
  for (const run of runs) {
    if (run.status !== 'succeeded') continue;
    total += 1 + run.messages.length;
    boundaries.push([total, run.id]);
  }
  const placed = new Map<string, Compaction[]>();
  const fallback = runs.at(-1)?.id;
  for (const item of compactions) {
    const id = boundaries.find(([count]) => count >= item.covered_messages)?.[1] ?? fallback;
    if (id) placed.set(id, [...(placed.get(id) ?? []), item]);
  }
  return placed;
}
