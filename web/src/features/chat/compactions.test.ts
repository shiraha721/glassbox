import { describe, expect, it } from 'vitest';
import type { Run } from '../../api/client';
import { compactionsAfter, type Compaction } from './compactions';

const run = (id: string, status: string, messages: number) =>
  ({ id, status, messages: Array.from({ length: messages }, () => ({})) }) as unknown as Run;
const compaction = (id: string, covered: number) =>
  ({ id, covered_messages: covered }) as unknown as Compaction;

describe('compactionsAfter', () => {
  const runs = [run('a', 'succeeded', 1), run('b', 'failed', 3), run('c', 'succeeded', 3)];

  it('places a summary after the turn where its covered history ends', () => {
    // a adds its task and one reply (2 messages); b failed and added nothing; c adds 4.
    const placed = compactionsAfter(runs, [compaction('x', 2), compaction('y', 6)]);
    expect(placed.get('a')?.map((item) => item.id)).toEqual(['x']);
    expect(placed.get('c')?.map((item) => item.id)).toEqual(['y']);
    expect(placed.has('b')).toBe(false);
  });

  it('puts a summary it cannot place after the latest turn', () => {
    expect(compactionsAfter(runs, [compaction('z', 99)]).get('c')).toHaveLength(1);
    expect(compactionsAfter([], [compaction('z', 1)]).size).toBe(0);
  });
});
