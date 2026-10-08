import type { CommandInfo } from './commands';

/** The `/command` or `$skill` name being typed at the caret, and the text it would replace. */
export type Trigger = {
  kind: 'command' | 'skill';
  /** Index of the leading `/` or `$`. */
  start: number;
  /** End of the partly typed name, including name characters after the caret. */
  end: number;
  query: string;
};

export type Suggestion = {
  kind: 'command' | 'skill';
  name: string;
  description: string;
  hint: string;
  scope: string;
};

type SkillEntry = { name: string; description: string; scope?: string };

const NAME = /[a-z0-9-]*/y;

function nameEnd(text: string, from: number): number {
  NAME.lastIndex = from;
  NAME.exec(text);
  return NAME.lastIndex;
}

/** Find the trigger at the caret. A command is the whole input so far (`/rev|`), as the
 * server expands only a leading command; a Skill mention starts a word anywhere (`use $deb|`). */
export function triggerAt(text: string, caret: number): Trigger | null {
  const before = text.slice(0, caret);
  const command = /^\/([a-z0-9-]*)$/.exec(before);
  if (command) return { kind: 'command', start: 0, end: nameEnd(text, caret), query: command[1] };
  const skill = /(^|\s)\$([a-z0-9-]*)$/.exec(before);
  if (skill) {
    const start = caret - skill[2].length - 1;
    return { kind: 'skill', start, end: nameEnd(text, caret), query: skill[2] };
  }
  return null;
}

/** Names starting with the query first, then names containing it; at most eight. */
function ranked<T extends { name: string }>(items: T[], query: string): T[] {
  const starts = items.filter((item) => item.name.startsWith(query));
  const contains = items.filter(
    (item) => !item.name.startsWith(query) && query && item.name.includes(query),
  );
  return [...starts, ...contains].slice(0, 8);
}

/** The completions for a trigger from the workspace's commands or Skills. */
export function suggestionsFor(
  trigger: Trigger | null,
  commands: CommandInfo[],
  skills: SkillEntry[],
): Suggestion[] {
  if (!trigger) return [];
  if (trigger.kind === 'command')
    return ranked(commands, trigger.query).map((item) => ({
      kind: 'command',
      name: item.name,
      description: item.description,
      hint: item.argument_hint,
      scope: item.scope,
    }));
  return ranked(skills, trigger.query).map((item) => ({
    kind: 'skill',
    name: item.name,
    description: item.description,
    hint: '',
    scope: item.scope ?? 'project',
  }));
}

/** Replace the typed name with the chosen one, followed by a single space. */
export function applySuggestion(
  text: string,
  trigger: Trigger,
  name: string,
): { text: string; caret: number } {
  const token = `${trigger.kind === 'command' ? '/' : '$'}${name}`;
  const rest = text.slice(trigger.end);
  const spaced = rest.startsWith(' ') ? rest : ` ${rest}`;
  return {
    text: text.slice(0, trigger.start) + token + spaced,
    caret: trigger.start + token.length + 1,
  };
}
