import { describe, expect, it } from 'vitest';
import type { CommandInfo } from './commands';
import { sendsTask } from './sendKey';
import { applySuggestion, suggestionsFor, triggerAt } from './suggestions';

const command = (name: string): CommandInfo => ({
  name,
  description: '',
  argument_hint: '',
  scope: 'project',
});
const commands = [command('review'), command('release-notes'), command('fix-review')];
const skills = [
  { name: 'debugging', description: 'Reproduce first', scope: 'builtin' },
  { name: 'code-review', description: 'Find bugs', scope: 'builtin' },
];
const names = (text: string, caret = text.length) =>
  suggestionsFor(triggerAt(text, caret), commands, skills).map((item) => item.name);

describe('completion triggers', () => {
  it('completes a leading command while its name is typed', () => {
    expect(names('/re')).toEqual(['review', 'release-notes', 'fix-review']);
    expect(names('/')).toHaveLength(3);
    expect(names('/review a.py')).toEqual([]);
    expect(names('please /re')).toEqual([]);
  });

  it('completes a Skill mention anywhere a word starts', () => {
    expect(names('$')).toEqual(['debugging', 'code-review']);
    expect(names('use $rev')).toEqual(['code-review']);
    expect(names('cost$deb')).toEqual([]);
    expect(names('use $debugging now', 8)).toEqual(['debugging']);
  });

  it('replaces the whole typed name and leaves one space after it', () => {
    const typed = 'use $deb and $co';
    const first = applySuggestion(typed, triggerAt(typed, 8)!, 'debugging');
    expect(first).toEqual({ text: 'use $debugging and $co', caret: 15 });
    const command = applySuggestion('/re', triggerAt('/re', 3)!, 'review');
    expect(command).toEqual({ text: '/review ', caret: 8 });
    const middle = applySuggestion('/rev a.py', triggerAt('/rev a.py', 2)!, 'review');
    expect(middle.text).toBe('/review a.py');
  });
});

describe('send key', () => {
  const press = (key: string, extra: Partial<Parameters<typeof sendsTask>[0]> = {}) => ({
    key,
    shiftKey: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    ...extra,
  });

  it('runs on Enter by default and keeps Shift+Enter for new lines', () => {
    expect(sendsTask(press('Enter'), 'enter')).toBe(true);
    expect(sendsTask(press('Enter', { shiftKey: true }), 'enter')).toBe(false);
    expect(sendsTask(press('Enter', { ctrlKey: true }), 'enter')).toBe(true);
  });

  it('needs Ctrl or Cmd in the other mode', () => {
    expect(sendsTask(press('Enter'), 'mod-enter')).toBe(false);
    expect(sendsTask(press('Enter', { metaKey: true }), 'mod-enter')).toBe(true);
  });

  it('never runs while an input method is composing', () => {
    expect(sendsTask(press('Enter', { isComposing: true }), 'enter')).toBe(false);
    expect(sendsTask(press('a'), 'enter')).toBe(false);
  });
});
