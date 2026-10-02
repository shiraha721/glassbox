import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Composer } from './Composer';
import type { Block, Preset } from './api/client';

afterEach(cleanup);

const block: Block = {
  id: 'a',
  name: 'Identity',
  role: 'system',
  template: 'Analyze evidence.',
  enabled: true,
  placement: 'before_history',
  order: 10,
  variables: {},
  condition: null,
  provider_allowlist: null,
  model_allowlist: null,
};
const preset: Preset = {
  id: 'p',
  name: 'Analysis',
  revision: 1,
  blocks: [
    block,
    { ...block, id: 'b', name: 'Rules', order: 20 },
    { ...block, id: 'c', name: 'Context', placement: 'before_input', order: 30 },
  ],
};

describe('preset editing', () => {
  it('reorders inside a placement without changing message roles or the saved object', () => {
    const changed = vi.fn();
    render(
      <Composer
        preset={preset}
        presets={[preset]}
        dirty={false}
        busy={false}
        onChange={changed}
        onSelect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText('Move Identity down'));
    const next = changed.mock.calls[0][0] as Preset;
    expect(next.blocks.map((item) => item.id)).toEqual(['b', 'a', 'c']);
    expect(new Set(next.blocks.map((item) => item.order)).size).toBe(3);
    expect(next.blocks.every((item) => item.role === 'system')).toBe(true);
    expect(preset.blocks[0].id).toBe('a');
  });

  it('does not apply malformed advanced rules to the draft', () => {
    const changed = vi.fn();
    render(
      <Composer
        preset={preset}
        presets={[preset]}
        dirty={false}
        busy={false}
        onChange={changed}
        onSelect={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Identity rules'), { target: { value: '{ broken' } });
    fireEvent.click(screen.getAllByText('Apply rules')[0]);
    expect(changed).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toBeTruthy();
  });
});
