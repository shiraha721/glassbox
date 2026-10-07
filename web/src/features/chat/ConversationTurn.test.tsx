import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it } from 'vitest';
import { RunTurn } from './ConversationTurn';
import type { Run } from '../../api/client';

afterEach(cleanup);

const base: Run = {
  id: 'run-1',
  session_id: 'session-1',
  input: 'Fix the failing test.',
  workspace_root: '/work',
  status: 'succeeded',
  stop_reason: 'completed',
  messages: [],
  partial_output: '',
  latest_event_seq: 9,
  approval: null,
  error: null,
  created_at: '2026-10-06T10:00:00+00:00',
  updated_at: '2026-10-06T10:00:05+00:00',
  preset_name: 'Coding',
  provider: 'openai',
  provider_id: 'openai',
  model: 'gpt-5',
  reasoning_effort: 'high',
  access: 'workspace-write',
  usage: {},
  source_run_id: null,
  unknown_calls: [],
  plan: null,
  active_tool: null,
  changed_files: [],
  command: null,
  attachments: [],
};
const call = (id: string, name: string, args: Record<string, unknown>) => ({ id, name, args });
const assistant = (calls: ReturnType<typeof call>[], content = '') => ({
  role: 'assistant' as const,
  content,
  source: 'runtime',
  tool_calls: calls,
  tool_call_id: null,
  blocks: [],
  attachments: [],
});
const result = (id: string, content: unknown) => ({
  role: 'tool' as const,
  content: typeof content === 'string' ? content : JSON.stringify(content),
  source: 'runtime',
  tool_calls: [],
  tool_call_id: id,
  blocks: [],
  attachments: [],
});

function renderTurn(run: Run) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RunTurn
        run={run}
        index={0}
        busy={false}
        selected={false}
        canFork={false}
        onInspect={() => undefined}
        onFork={() => undefined}
        onApprove={() => undefined}
        onResume={() => undefined}
      />
    </QueryClientProvider>,
  );
}

describe('tool steps', () => {
  it('pairs each call with its result and classifies failures', () => {
    const { container } = renderTurn({
      ...base,
      messages: [
        assistant([
          call('a', 'read_text', { path: 'src/app.py' }),
          call('b', 'bash', { command: 'pytest -q\necho done' }),
          call('c', 'write_text', { path: '.env', content: 'x' }),
        ]),
        result('a', { path: 'src/app.py', content: 'print(1)\n', start_line: 1, end_line: 1 }),
        result('b', { stdout: '1 failed', stderr: '', exit_code: 1, timed_out: false }),
        result('c', { error: { code: 'tool_denied', message: 'Credential paths are excluded.' } }),
        assistant([], 'The test fails because of an off-by-one error.'),
      ],
    });
    const steps = [...container.querySelectorAll('.tool-step')];
    expect(steps.map((step) => step.getAttribute('data-state'))).toEqual([
      'done',
      'failed',
      'failed',
    ]);
    expect(steps[0].textContent).toContain('src/app.py');
    // Only the first command line summarizes a multi-line command.
    expect(steps[1].querySelector('.tool-step-target')?.textContent).toBe('pytest -q');
    expect(steps[1].textContent).toContain('exit 1');
    expect(steps[2].textContent).toContain('tool denied');
    expect(screen.getByText('Credential paths are excluded.')).toBeTruthy();
    expect(screen.getByText(/off-by-one/)).toBeTruthy();
  });

  it('shows the pending approval and live output of unfinished calls', () => {
    const waiting = renderTurn({
      ...base,
      status: 'waiting_approval',
      messages: [assistant([call('a', 'start_process', { command: 'npm run dev' })])],
      approval: {
        id: 'run-1:tool:0000:0000',
        tool_call_id: 'a',
        name: 'start_process',
        args: { command: 'npm run dev' },
        decision: null,
      },
    });
    expect(waiting.container.querySelector('.tool-step')?.getAttribute('data-state')).toBe(
      'waiting',
    );
    expect(screen.getByRole('button', { name: 'Approve once' })).toBeTruthy();
    cleanup();

    const running = renderTurn({
      ...base,
      status: 'running',
      messages: [assistant([call('a', 'bash', { command: 'make test' })])],
      active_tool: { id: 'run-1:tool:0000:0000', name: 'bash', stdout: 'compiling…', stderr: '' },
    });
    const step = running.container.querySelector('.tool-step');
    expect(step?.getAttribute('data-state')).toBe('running');
    expect(step?.hasAttribute('open')).toBe(true);
    expect(screen.getByText('compiling…')).toBeTruthy();
  });

  it('renders patch diffs with added and removed lines', () => {
    const { container } = renderTurn({
      ...base,
      messages: [
        assistant([
          call('a', 'apply_patch', {
            patch: '*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n*** End Patch',
          }),
        ]),
        result('a', {
          files: [{ path: 'a.py', operation: 'update', diff: '@@ -1 +1 @@\n-x = 1\n+x = 2\n' }],
          atomic: false,
        }),
      ],
    });
    expect(container.querySelector('.tool-step-target')?.textContent).toBe('a.py');
    expect(container.querySelector('.diff-add')?.textContent).toContain('+x = 2');
    expect(container.querySelector('.diff-remove')?.textContent).toContain('-x = 1');
  });

  it('lists the files a finished run changed, with undo', () => {
    renderTurn({ ...base, changed_files: ['README.md', 'notes.txt'] });
    expect(screen.getByText('Changed 2 files')).toBeTruthy();
    expect(screen.getByRole('list', { name: 'Changed files' }).textContent).toContain('notes.txt');
    // Disabled until the current state of each file is known.
    expect(
      (screen.getByRole('button', { name: 'Undo file changes' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('offers no undo while the run is still active', () => {
    renderTurn({ ...base, status: 'running', changed_files: ['README.md'] });
    expect(screen.queryByText('Changed 1 file')).toBeNull();
  });

  it('shows a command as typed, with the text it was sent as', () => {
    renderTurn({
      ...base,
      input: 'Review a.py carefully.',
      command: { name: 'review', arguments: 'a.py', scope: 'project', hash: 'h' },
    });
    expect(screen.getByText('/review a.py')).toBeTruthy();
    expect(screen.getByText('Sent as')).toBeTruthy();
    expect(screen.getByText('Review a.py carefully.')).toBeTruthy();
  });
});
