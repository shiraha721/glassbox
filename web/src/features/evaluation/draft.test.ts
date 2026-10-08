import { describe, expect, it } from 'vitest';
import type { Bootstrap, EvalVariant } from '../../api/client';
import {
  blank,
  identicalSetups,
  isRenamed,
  namedSetup,
  problems,
  setupLabel,
  uniqueName,
} from './draft';

const data = {
  presets: [
    { id: 'security', name: 'Security analysis' },
    { id: 'coding', name: 'Coding' },
  ],
  evals: [{ name: 'Evaluation 1' }],
} as unknown as Bootstrap;

const setup = (change: Partial<EvalVariant> = {}): EvalVariant => ({
  id: crypto.randomUUID(),
  name: '',
  preset_id: 'security',
  model: null,
  access: null,
  ...change,
});

describe('evaluation drafts', () => {
  it('starts with one task and two setups that differ', () => {
    const draft = blank(data);
    expect(draft.name).toBe('Evaluation 2');
    expect(draft.cases).toHaveLength(1);
    expect(draft.variants.map((item) => item.name)).toEqual(['Security analysis', 'Coding']);
    expect(identicalSetups(draft.variants)).toEqual([]);
  });

  it('names a setup after what it runs, uniquely', () => {
    const model = { provider_id: 'openai', model: 'gpt-x', reasoning_effort: null };
    const chosen = setup({ preset_id: 'coding', model, access: 'read-only' });
    expect(setupLabel(chosen, data)).toBe('Coding · gpt-x · read only');
    const first = namedSetup(setup(), [], data);
    expect(namedSetup(setup(), [first], data).name).toBe('Security analysis (2)');
    expect(uniqueName('Task 1', ['task 1', 'Task 1 (2)'])).toBe('Task 1 (3)');
    expect(isRenamed(first, data)).toBe(false);
    expect(isRenamed({ ...first, name: 'Baseline' }, data)).toBe(true);
  });

  it('warns about identical setups and incomplete tasks before running', () => {
    expect(identicalSetups([setup(), setup({ preset_id: 'coding' }), setup()])).toEqual([[0, 2]]);
    const effort = (reasoning_effort: 'low' | 'high') =>
      setup({ model: { provider_id: 'openai', model: 'gpt-x', reasoning_effort } });
    expect(identicalSetups([effort('low'), effort('high')])).toEqual([]);
    const draft = blank(data);
    expect(problems(draft)).toEqual(['Task 1 has no text to send.']);
    draft.cases[0].input = 'Find the entry point.';
    expect(problems(draft)).toEqual(['Task 1 needs the text its reply is checked for.']);
    draft.cases[0].check = { kind: 'succeeds', value: '', ignore_case: true };
    expect(problems(draft)).toEqual([]);
  });
});
