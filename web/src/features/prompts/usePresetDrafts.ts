import { useState } from 'react';
import type { Preset } from '../../api/client';

type Draft = {
  present: Preset;
  saved: Preset;
  past: Preset[];
  future: Preset[];
  lastGroup?: string;
  lastEdit?: number;
};

/** Keep independent preset drafts and bounded undo history while users switch presets. */
export function usePresetDrafts(savedPresets: Preset[]) {
  const [selected, setSelected] = useState(savedPresets[0].id);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const preset = drafts[selected]?.present || savedPresets.find((item) => item.id === selected)!;
  const entry = drafts[selected];
  const baseline = entry?.saved || preset;
  const dirty = preset.revision === 0 || JSON.stringify(preset) !== JSON.stringify(baseline);
  const presets = [
    ...new Map([
      ...savedPresets.map((item) => [item.id, drafts[item.id]?.present || item] as const),
      ...Object.values(drafts).map((item) => [item.present.id, item.present] as const),
    ]).values(),
  ];

  /** Coalesce typing in the same field; structural edits remain distinct undo steps. */
  const change = (next: Preset, group?: string) => {
    const stamp = Date.now();
    setDrafts((current) => {
      const previous = current[next.id];
      const saved = previous?.saved || savedPresets.find((item) => item.id === next.id) || next;
      const before = previous?.present || saved;
      if (JSON.stringify(before) === JSON.stringify(next) && previous) return current;
      const coalesced =
        group && previous?.lastGroup === group && stamp - (previous.lastEdit || 0) < 700;
      return {
        ...current,
        [next.id]: {
          present: { ...next, revision: saved.revision },
          saved,
          past: coalesced
            ? previous.past
            : previous || saved.revision > 0
              ? [...(previous?.past || []), before].slice(-60)
              : [],
          future: [],
          lastGroup: group,
          lastEdit: stamp,
        },
      };
    });
    setSelected(next.id);
  };

  /** Move across snapshots using the latest saved revision for subsequent API edits. */
  const travel = (direction: 'undo' | 'redo') => {
    setDrafts((current) => {
      const draft = current[selected];
      if (!draft) return current;
      const stack = direction === 'undo' ? draft.past : draft.future;
      const next = stack.at(-1);
      if (!next) return current;
      return {
        ...current,
        [selected]: {
          ...draft,
          present: { ...next, revision: draft.saved.revision },
          lastGroup: undefined,
          past: direction === 'undo' ? stack.slice(0, -1) : [...draft.past, draft.present],
          future: direction === 'redo' ? stack.slice(0, -1) : [...draft.future, draft.present],
        },
      };
    });
  };

  /** A successful save advances the baseline without erasing reversible editor history. */
  const commit = (saved: Preset) =>
    setDrafts((current) => ({
      ...current,
      [saved.id]: {
        ...current[saved.id],
        saved,
        present: saved,
        past: current[saved.id]?.past || [],
        future: [],
        lastGroup: undefined,
      },
    }));

  /** Discard this draft only; other presets keep their edits and selection remains usable. */
  const discard = () => {
    if (baseline.revision === 0) {
      setDrafts((current) => {
        const remaining = { ...current };
        delete remaining[selected];
        return remaining;
      });
      setSelected(savedPresets[0].id);
    } else change(baseline);
  };

  return {
    preset,
    presets,
    dirty,
    change,
    commit,
    discard,
    select: (next: Preset) => setSelected(next.id),
    undo: () => travel('undo'),
    redo: () => travel('redo'),
    canUndo: !!entry?.past.length,
    canRedo: !!entry?.future.length,
  };
}
