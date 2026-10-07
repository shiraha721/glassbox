import { describe, expect, it } from 'vitest';
import { matchingCommands, type CommandInfo } from './commands';

const command = (name: string): CommandInfo => ({
  name,
  description: '',
  argument_hint: '',
  scope: 'project',
});

describe('matchingCommands', () => {
  const commands = [command('review'), command('release-notes'), command('fix')];

  it('suggests commands while the name is being typed', () => {
    expect(matchingCommands('/re', commands).map((item) => item.name)).toEqual([
      'review',
      'release-notes',
    ]);
    expect(matchingCommands('/', commands)).toHaveLength(3);
  });

  it('stops suggesting once arguments start or for other input', () => {
    expect(matchingCommands('/review a.py', commands)).toEqual([]);
    expect(matchingCommands('review', commands)).toEqual([]);
    expect(matchingCommands('/zzz', commands)).toEqual([]);
  });
});
