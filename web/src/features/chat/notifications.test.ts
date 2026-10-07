import { describe, expect, it } from 'vitest';
import { runNotice } from './notifications';

describe('runNotice', () => {
  const run = { status: 'waiting_approval', input: 'Fix the parser' };

  it('ignores the first sighting of a Run, such as after a page load', () => {
    expect(runNotice(undefined, { ...run, status: 'succeeded' }, 'Chat')).toBeNull();
  });

  it('announces approvals and endings, not intermediate states', () => {
    expect(runNotice('running', run, 'Chat')).toEqual({
      title: 'Run needs your approval',
      body: 'Chat: Fix the parser',
    });
    expect(runNotice('running', { ...run, status: 'failed' }, 'Chat')?.title).toBe('Run failed');
    expect(runNotice('queued', { ...run, status: 'running' }, 'Chat')).toBeNull();
    expect(runNotice('succeeded', { ...run, status: 'succeeded' }, 'Chat')).toBeNull();
  });

  it('shortens long tasks', () => {
    const notice = runNotice('running', { status: 'succeeded', input: 'x'.repeat(300) }, 'C');
    expect(notice?.body.length).toBeLessThan(130);
  });
});
