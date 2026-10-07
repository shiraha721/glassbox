import type { components } from '../../api/generated';

type GitDiff = components['schemas']['GitDiff'];

// Run input is limited to 100,000 characters; leave room for the instructions.
const DIFF_BUDGET = 90_000;

/** A code-review task that carries the diff itself, since the agent cannot run Git. */
export function reviewPrompt(diff: GitDiff): string {
  let text = diff.diff;
  let cut = diff.truncated;
  if (text.length > DIFF_BUDGET) {
    text = text.slice(0, text.lastIndexOf('\n', DIFF_BUDGET) + 1);
    cut = true;
  }
  const longest = Math.max(2, ...(text.match(/`+/g) ?? []).map((run) => run.length));
  const fence = '`'.repeat(longest + 1);
  const notes = [
    cut && 'The diff was cut short; changes after the cut are not shown.',
    diff.omitted.length > 0 &&
      `Also changed but not shown (private or unreadable): ${diff.omitted.join(', ')}.`,
  ].filter(Boolean);
  return [
    'Review the uncommitted changes in this workspace: the diff against HEAD is below.',
    'Report concrete bugs, security problems and risky changes with file and line, and say',
    'what you checked. Read the files in the workspace for context where the diff is not enough.',
    ...notes,
    '',
    `${fence}diff`,
    text.trimEnd(),
    fence,
  ].join('\n');
}
