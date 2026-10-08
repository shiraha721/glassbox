import type { Bootstrap, Eval, EvalCase, EvalCheck, EvalVariant } from '../../api/client';

export type Draft = {
  id: string | null;
  revision: number;
  name: string;
  cases: EvalCase[];
  variants: EvalVariant[];
};

/** How a task's final reply is judged, phrased to follow "The reply passes if it …". */
export const checkKinds: { value: EvalCheck['kind']; label: string }[] = [
  { value: 'contains', label: 'contains' },
  { value: 'not_contains', label: 'does not contain' },
  { value: 'equals', label: 'is exactly' },
  { value: 'regex', label: 'matches the pattern' },
  { value: 'succeeds', label: 'exists (the run only has to finish)' },
];

const accessLabels: Record<string, string> = {
  'read-only': 'read only',
  'workspace-write': 'workspace write',
};

/** Add " (2)", " (3)" … until the name differs from every taken one, ignoring case. */
export function uniqueName(base: string, taken: string[]): string {
  const used = new Set(taken.map((item) => item.trim().toLowerCase()));
  let name = base;
  for (let index = 2; used.has(name.toLowerCase()); index += 1) name = `${base} (${index})`;
  return name;
}

/** Describe a setup by what it runs: its preset, then any model or access it overrides. */
export function setupLabel(variant: EvalVariant, data: Bootstrap): string {
  const preset = data.presets.find((item) => item.id === variant.preset_id)?.name ?? 'Preset';
  const parts = [preset];
  if (variant.model) parts.push(variant.model.model);
  if (variant.access) parts.push(accessLabels[variant.access] ?? variant.access);
  return parts.join(' · ');
}

/** Give a setup the name its choices describe, unique among the other setups. */
export function namedSetup(variant: EvalVariant, others: EvalVariant[], data: Bootstrap) {
  return {
    ...variant,
    name: uniqueName(
      setupLabel(variant, data),
      others.filter((item) => item.id !== variant.id).map((item) => item.name),
    ),
  };
}

/** Whether a setup's name was chosen by the user rather than derived from its choices. */
export function isRenamed(variant: EvalVariant, data: Bootstrap): boolean {
  return !variant.name.startsWith(setupLabel(variant, data));
}

export function newCase(index: number): EvalCase {
  return {
    id: crypto.randomUUID(),
    name: `Task ${index}`,
    input: '',
    variables: {},
    check: { kind: 'contains', value: '', ignore_case: true },
  };
}

export function newSetup(presetId: string, others: EvalVariant[], data: Bootstrap): EvalVariant {
  const variant = {
    id: crypto.randomUUID(),
    name: '',
    preset_id: presetId,
    model: null,
    access: null,
  };
  return namedSetup(variant, others, data);
}

/** A new evaluation: one task and two setups that differ, so a first run compares something. */
export function blank(data: Bootstrap): Draft {
  const first = newSetup(data.presets[0]?.id ?? '', [], data);
  const second = newSetup(data.presets[1]?.id ?? first.preset_id, [first], data);
  return {
    id: null,
    revision: 0,
    name: uniqueName(
      `Evaluation ${data.evals.length + 1}`,
      data.evals.map((item) => item.name),
    ),
    cases: [newCase(1)],
    variants: [first, second],
  };
}

export function fromEval(value: Eval): Draft {
  return {
    id: value.id,
    revision: value.revision,
    name: value.name,
    cases: value.cases,
    variants: value.variants,
  };
}

/** Pairs of setups that would run identically, so their results differ only by chance. */
export function identicalSetups(variants: EvalVariant[]): [number, number][] {
  const key = (item: EvalVariant) =>
    JSON.stringify([
      item.preset_id,
      item.model?.provider_id,
      item.model?.model,
      item.model?.reasoning_effort,
      item.access,
    ]);
  const pairs: [number, number][] = [];
  variants.forEach((item, index) =>
    variants.slice(0, index).forEach((other, before) => {
      if (key(item) === key(other)) pairs.push([before, index]);
    }),
  );
  return pairs;
}

/** What is missing before the evaluation can run, as one sentence per problem. */
export function problems(draft: Draft): string[] {
  const found: string[] = [];
  draft.cases.forEach((item, index) => {
    if (!item.input.trim()) found.push(`Task ${index + 1} has no text to send.`);
    else if (item.check?.kind !== 'succeeds' && !item.check?.value.trim())
      found.push(`Task ${index + 1} needs the text its reply is checked for.`);
  });
  return found;
}
