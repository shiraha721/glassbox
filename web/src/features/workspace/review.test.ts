import { describe, expect, it } from 'vitest';
import { reviewPrompt } from './review';

describe('reviewPrompt', () => {
  it('fences the diff so its own backticks cannot end the block', () => {
    const prompt = reviewPrompt({
      path: null,
      diff: 'diff --git a/a.md b/a.md\n+```js\n+code\n+```\n',
      truncated: false,
      omitted: ['.env'],
    });
    expect(prompt).toContain('````diff\ndiff --git a/a.md b/a.md');
    expect(prompt.trimEnd().endsWith('````')).toBe(true);
    expect(prompt).toContain('not shown (private or unreadable): .env.');
  });

  it('stays within the run input limit', () => {
    const line = '+' + 'x'.repeat(99) + '\n';
    const prompt = reviewPrompt({
      path: null,
      diff: line.repeat(2000),
      truncated: false,
      omitted: [],
    });
    expect(prompt.length).toBeLessThan(100_000);
    expect(prompt).toContain('The diff was cut short');
  });
});
