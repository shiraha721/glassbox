import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';

/** Open the local workspace directly and create a fresh conversation. */
async function openWorkspace(page: Page) {
  await page.goto('/');
  await expect(page.getByLabel('Conversation workspace')).toBeAttached();
  if (!(await page.getByRole('button', { name: 'New conversation', exact: true }).isVisible()))
    await page.getByRole('button', { name: 'Toggle sidebar', exact: true }).click();
  await page.getByRole('button', { name: 'New conversation', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Preview', exact: true })).toBeEnabled();
}

/** Submit tasks directly; preview is an optional inspection action. */
async function startTask(page: Page, input: string) {
  await page.getByLabel('Task', { exact: true }).fill(input);
  await page.getByRole('button', { name: 'Run', exact: true }).click();
}

test('daily work compacts history, reuses preset packages and controls a native pipe process', async ({
  page,
}) => {
  test.setTimeout(90_000);
  await openWorkspace(page);
  for (const turn of [
    'First source decision.',
    'Second verified decision.',
    'Keep this recent turn.',
  ]) {
    await startTask(page, turn);
    await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  }
  await page.getByRole('button', { name: 'Context', exact: true }).click();
  const context = page.getByLabel('Conversation context', { exact: true });
  await context.getByLabel('Keep recent turns').selectOption('1');
  await context.getByRole('button', { name: 'Compact older turns' }).click();
  await expect(context.getByText('Active summary', { exact: true })).toBeVisible();
  await expect(page.locator('.conversation-turn')).toHaveCount(3);
  await context.getByRole('button', { name: 'Restore full context' }).click();
  await expect(context.getByText('Active summary', { exact: true })).not.toBeAttached();
  await context.getByRole('button', { name: 'Close context' }).click();
  await page.getByRole('button', { name: 'Prompt', exact: true }).click();
  const boot = await (await page.request.get('/api/v1/bootstrap')).json();
  const packageData = {
    format: 'rtagent.preset.v1',
    preset: {
      ...boot.presets.find((item: { id: string }) => item.id === 'coding'),
      name: 'Imported browser coding',
    },
  };
  await page.getByLabel('Import preset package', { exact: true }).setInputFiles({
    name: 'coding.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(packageData)),
  });
  await expect(page.getByLabel('Active preset', { exact: true })).toHaveValue(/^[a-f0-9]{32}$/);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export preset', exact: true }).click();
  expect((await download).suggestedFilename()).toContain('preset');
  await page.getByRole('button', { name: 'Close prompt' }).click();
  const shell =
    process.platform === 'win32'
      ? "[Console]::WriteLine('browser-ready'); $line=[Console]::ReadLine(); [Console]::WriteLine('received:'+$line); Start-Sleep -Seconds 60"
      : "printf 'browser-ready\\n'; read -r line; printf 'received:%s\\n' \"$line\"; sleep 60";
  await startTask(page, '/tool start_process ' + JSON.stringify({ command: shell, timeout: 60 }));
  await expect(page.getByTestId('run-status')).toHaveText('waiting approval');
  await page.getByRole('button', { name: 'Approve once', exact: true }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await page.getByRole('button', { name: 'Processes', exact: true }).click();
  const processes = page.getByLabel('Conversation processes', { exact: true });
  await expect(processes.locator('.process-output')).toContainText('browser-ready');
  await processes.getByLabel('Pipe input', { exact: true }).fill('browser input λ');
  await processes.getByRole('button', { name: 'Send line', exact: true }).click();
  await expect(processes.locator('.process-output')).toContainText('received:browser input λ');
  await processes.getByRole('button', { name: 'Stop process', exact: true }).click();
  await expect(processes.getByText('stopped', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/daily-processes.png', fullPage: true });
});

test('tools expose schemas and effective policy, presets and recorded plans remain inspectable', async ({
  page,
}) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Tools', exact: true }).click();
  const catalog = page.getByLabel('Tool catalog', { exact: true });
  await catalog.getByLabel('Search tools', { exact: true }).fill('apply_patch');
  await expect(catalog.locator('[data-tool="apply_patch"]')).toBeVisible();
  await catalog.getByText('Arguments', { exact: true }).click();
  await expect(catalog.getByText('"expected_hashes"', { exact: false })).toBeVisible();
  await catalog.getByRole('button', { name: 'Edit permissions', exact: true }).click();
  const settings = page.getByRole('dialog');
  await settings
    .getByRole('combobox', { name: 'Workspace access', exact: true })
    .selectOption('read-only');
  await settings.getByLabel('Search tools', { exact: true }).fill('apply_patch');
  await expect(settings.getByText('Blocked by read-only', { exact: true })).toBeVisible();
  await settings.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(
    settings.getByText('Settings saved. New runs use these settings.', { exact: true }),
  ).toBeVisible();
  await settings.getByRole('button', { name: 'Close settings', exact: true }).click();
  try {
    await page.getByRole('button', { name: 'Prompt', exact: true }).click();
    await page.getByLabel('Active preset', { exact: true }).selectOption({ label: 'Coding' });
    await page.getByText('Implementation workflow', { exact: true }).click();
    await expect(page.getByLabel('Implementation workflow template')).toBeVisible();
    await page.getByRole('button', { name: 'Preview', exact: true }).click();
    await expect(page.getByText('Live draft', { exact: true })).toBeVisible();
    const current = await (await page.request.get('/api/v1/bootstrap')).json();
    const inspected = await (
      await page.request.post('/api/v1/prompts/inspect', {
        data: {
          preset: current.presets.find((item: { id: string }) => item.id === 'coding'),
          config_revision: current.config.revision,
        },
      })
    ).json();
    expect(
      inspected.tools.some((item: { name: string }) => item.name === 'apply_patch'),
    ).toBeFalsy();
    expect(
      inspected.compiled.messages.some(
        (item: { source: string }) => item.source === 'runtime:environment',
      ),
    ).toBeTruthy();
    await page.getByRole('button', { name: 'Close inspector', exact: true }).click();
    await startTask(
      page,
      '/tool update_plan {"explanation":"Check current task progress","plan":[{"step":"Inspect workspace","status":"completed"},{"step":"Verify behavior","status":"in_progress"}]}',
    );
    await expect(page.getByTestId('run-status')).toHaveText('succeeded');
    await expect(page.locator('.task-plan')).toContainText('Verify behavior');
    await expect(page.locator('.task-plan [data-status="in_progress"]')).toContainText(
      'Verify behavior',
    );
    await page.reload();
    await expect(page.locator('.task-plan')).toContainText('Check current task progress');
  } finally {
    const state = await (await page.request.get('/api/v1/bootstrap')).json();
    const restored = await page.request.put('/api/v1/config', {
      data: { ...state.config, sandbox_mode: 'workspace-write' },
    });
    expect(restored.ok()).toBeTruthy();
  }
});

test('projects bind conversations, tools and memory to the selected workspace folder', async ({
  page,
}) => {
  await openWorkspace(page);
  const boot = await (await page.request.get('/api/v1/bootstrap')).json();
  const root = path.join(path.dirname(boot.workspace_root), 'browser-project');
  mkdirSync(root, { recursive: true });
  writeFileSync(path.join(root, 'README.md'), 'Browser project evidence.');
  writeFileSync(path.join(root, 'AGENTS.md'), 'Use this project folder only.');
  await page.getByRole('button', { name: 'Add project', exact: true }).click();
  await page.getByLabel('Project name', { exact: true }).fill('Browser project');
  await page.getByLabel('Workspace folder', { exact: true }).fill(root);
  await page.getByRole('dialog').getByRole('button', { name: 'Add project', exact: true }).click();
  await expect(page.getByRole('dialog')).not.toBeAttached();
  await expect(page.getByRole('button', { name: 'Browser project', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await page.getByRole('button', { name: 'New conversation', exact: true }).click();
  await startTask(page, '/read README.md');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.markdown')).toContainText('Browser project evidence.');
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await page.getByLabel('Project memory', { exact: true }).fill('Browser project notes.');
  await page.getByRole('button', { name: 'Save memory', exact: true }).click();
  await expect(page.getByText('Memory saved. New runs will use it.')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Browser project', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.locator('.markdown')).toContainText('Browser project evidence.');
  await page.getByRole('button', { name: 'Inspector', exact: true }).click();
  await expect(page.getByLabel('Request inspector')).toContainText(`Run workspace: ${root}`);
  const session = (await (await page.request.get('/api/v1/bootstrap')).json()).sessions.find(
    (item: { project_id: string | null }) => item.project_id,
  );
  await page.getByLabel(`Conversation actions for ${session.title}`, { exact: true }).click();
  await page.getByRole('menuitem', { name: 'Change workspace', exact: true }).click();
  await page.getByLabel('Conversation project', { exact: true }).selectOption('');
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Default workspace', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.conversation-turn')).toHaveCount(1);
  await startTask(page, '/read README.md');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.markdown').last()).toContainText(
    'Local MCP and browser test workspace.',
  );
  await page.screenshot({ path: 'test-results/project-workspace.png', fullPage: true });
});

test('live preview follows input, variables, preset and Skills without saving drafts', async ({
  page,
}) => {
  await openWorkspace(page);
  const before = await (await page.request.get('/api/v1/bootstrap')).json();
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  const inspector = page.getByLabel('Request inspector');
  await expect(inspector.getByText('Live draft', { exact: true })).toBeVisible();
  await expect(inspector).toContainText('workspace:AGENTS.md');
  await expect(inspector.getByText('(No task input)', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Prompt', exact: true }).click();
  await page.getByLabel('Base identity template').fill('Current target: {{target}}');
  await page
    .locator('.block')
    .filter({ has: page.getByLabel('Base identity template') })
    .getByText('Variables & activation rules', { exact: true })
    .click();
  await page.getByLabel('Base identity rules').fill(
    JSON.stringify({
      variables: { target: { type: 'string', required: true, default: null } },
      condition: null,
      provider_allowlist: null,
      model_allowlist: null,
    }),
  );
  await page.getByRole('button', { name: 'Apply rules', exact: true }).first().click();
  const context = page.getByRole('region', { name: 'Workspace prompt context' });
  await context.getByText('AGENTS.md', { exact: true }).click();
  await expect(context).toContainText('Development Guide');
  await page.getByLabel('Include skill-creator in prompt').check();
  await page.getByText('Input variables', { exact: false }).first().click();
  await page.getByLabel('Input variables JSON').fill('{"target":"first"}');
  await page.getByLabel('Task', { exact: true }).fill('Old input');
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(inspector.getByText('Live draft', { exact: true })).toBeVisible();
  await inspector.getByText('identity', { exact: true }).click();
  await expect(inspector).toContainText('Current target: first');
  await expect(inspector).toContainText('skill:skill-creator');
  await page.getByLabel('Task', { exact: true }).fill('Latest input');
  await page.getByLabel('Input variables JSON').fill('{"target":"second"}');
  await expect(inspector).toContainText('Latest input');
  await expect(inspector).toContainText('Current target: second');
  await page.getByLabel('Input variables JSON').fill('{broken');
  await expect(inspector.getByRole('alert')).toContainText('Showing the last valid preview');
  await expect(inspector).toContainText('Current target: second');
  await page.getByLabel('Input variables JSON').fill('{"target":"final"}');
  await expect(inspector).toContainText('Current target: final');
  await page.getByLabel('Task', { exact: true }).fill('');
  await expect(inspector.getByText('(No task input)', { exact: true })).toBeVisible();
  await page.getByLabel('Task', { exact: true }).fill('Final task');
  await expect(inspector.getByText('Final task', { exact: true })).toBeVisible();
  const unsaved = await (await page.request.get('/api/v1/bootstrap')).json();
  expect(unsaved.presets).toEqual(before.presets);
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await page.getByRole('button', { name: 'Inspector', exact: true }).click();
  await inspector.getByText('Prepared request', { exact: true }).first().click();
  await expect(inspector).toContainText('Current target: final');
  await expect(inspector).toContainText('Final task');
});

test('edit, preview, real file tool, inspect trace, and refresh without another run', async ({
  page,
}) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Prompt', exact: true }).click();
  await page
    .getByLabel('Base identity template')
    .fill('Analyze only with recorded evidence. Browser contract marker.');
  await page.getByLabel('Task', { exact: true }).fill('/read README.md');
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.getByText('Live draft', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.markdown')).toContainText('Local MCP and browser test workspace.');
  await page.getByRole('button', { name: 'Inspector', exact: true }).click();
  await page.getByText('Prepared request', { exact: true }).first().click();
  await expect(page.getByLabel('Request inspector')).toContainText('Browser contract marker');
  await expect(page.getByRole('link', { name: 'Export JSON' })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('#run-selector option')).toHaveCount(1);
  await page.screenshot({ path: 'test-results/completed-workbench.png', fullPage: true });
});

test('approval survives a browser refresh and only one tool result is recorded', async ({
  page,
}) => {
  await openWorkspace(page);
  await startTask(page, '/echo durable browser approval');
  await expect(page.getByRole('button', { name: 'Approve once' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Approve once' })).toBeVisible();
  await page.getByRole('button', { name: 'Approve once' }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.tool-result')).toHaveCount(1);
  await expect(page.locator('#run-selector option')).toHaveCount(1);
});

test('reject a tool and cancel the next turn', async ({ page }) => {
  await openWorkspace(page);
  await startTask(page, '/echo rejected');
  await page.getByRole('button', { name: 'Reject', exact: true }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.markdown')).toContainText('tool_denied');
  await startTask(page, '/slow');
  await page.getByRole('button', { name: 'Stop run' }).click();
  await expect(page.getByTestId('run-status')).toHaveText('cancelled');
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Preview', exact: true })).toBeEnabled();
});

test('settings save real endpoint references and refresh the live preview', async ({ page }) => {
  await openWorkspace(page);
  await page.getByLabel('Task', { exact: true }).fill('configuration draft');
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('combobox', { name: 'Provider', exact: true }).selectOption('openai');
  await page.getByLabel('Base URL', { exact: true }).fill('http://127.0.0.1:11434/v1');
  await page.getByLabel('Model', { exact: true }).fill('local-contract-model');
  await page.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(page.getByText('Settings saved. New runs use these settings.')).toBeVisible();
  await page.getByLabel('Close settings').click();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('local-contract-model');
  await page.getByRole('combobox', { name: 'Provider', exact: true }).selectOption('fake');
  await page.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(page.getByText('Settings saved. New runs use these settings.')).toBeVisible();
});

test('discover a stdio MCP tool, authorize it, and complete the tool loop', async ({ page }) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Add server' }).click();
  const root = path.resolve(import.meta.dirname, '../..');
  await page
    .getByLabel('Executable', { exact: true })
    .fill(
      path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    );
  await page
    .getByLabel('Arguments · one per line', { exact: true })
    .fill(path.join(root, 'tests/fixtures/mcp_server.py'));
  await page.getByRole('button', { name: 'Save & discover' }).click();
  const permission = page.getByRole('combobox', { name: /mcp_.*fixture_echo.*permission/ });
  await expect(permission).toHaveValue('deny');
  const name = (await permission.getAttribute('aria-label'))!.replace(' permission', '');
  await permission.selectOption('ask');
  await page.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(page.getByText('Settings saved. New runs use these settings.')).toBeVisible();
  await page.getByLabel('Close settings').click();
  await startTask(page, `/tool ${name} {"text":"browser MCP evidence"}`);
  await page.getByRole('button', { name: 'Approve once' }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.markdown')).toContainText('MCP evidence: browser MCP evidence');
});

test('workspace memory and skills can be saved, selected, and used by direct Run', async ({
  page,
}) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await page.getByLabel('Project memory', { exact: true }).fill('Browser memory evidence.');
  await page.getByRole('button', { name: 'Save memory', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save memory', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Skills', exact: true }).click();
  await expect(page.getByLabel('Use skill-creator', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'New skill', exact: true }).click();
  await page.getByLabel('Skill name', { exact: true }).fill('browser-review');
  await page
    .getByLabel('Description', { exact: true })
    .fill('Review evidence for the browser check.');
  await page
    .getByLabel('Instructions', { exact: true })
    .fill('Browser skill evidence. Cite source paths.');
  await page.getByRole('button', { name: 'Save skill', exact: true }).click();
  await page.getByLabel('Use browser-review', { exact: true }).check();
  await page.getByRole('button', { name: 'Close workspace', exact: true }).click();
  await startTask(page, 'Check recorded workspace context');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await page.getByRole('button', { name: 'Inspector', exact: true }).click();
  await page.getByText('Prepared request', { exact: true }).first().click();
  await expect(page.getByLabel('Request inspector')).toContainText('Browser memory evidence.');
  await expect(page.getByLabel('Request inspector')).toContainText('Browser skill evidence.');
  await page.screenshot({ path: 'test-results/workspace-context.png', fullPage: true });
});

test('the workbench remains usable on a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openWorkspace(page);
  await page.getByLabel('Task', { exact: true }).fill('Check narrow layout');
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.getByText('Live draft', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeVisible();
  const width = await page.evaluate(() => ({
    content: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
  }));
  expect(width.content).toBeLessThanOrEqual(width.viewport);
  await page.getByRole('button', { name: 'Prompt', exact: true }).click();
  const composer = await page.getByLabel('Prompt composer').boundingBox();
  const save = await page.getByRole('button', { name: 'Save preset', exact: true }).boundingBox();
  expect(save!.y + save!.height).toBeLessThanOrEqual(composer!.y + composer!.height);
  await page.screenshot({ path: 'test-results/narrow-workbench.png', fullPage: true });
});

test('preset drafts remain switchable, with undo, redo, and discard', async ({ page }) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Prompt', exact: true }).click();
  const picker = page.getByLabel('Active preset', { exact: true });
  const originalId = await picker.inputValue();
  const originalText = await page.getByLabel('Base identity template').inputValue();
  await page.getByRole('button', { name: 'Duplicate preset', exact: true }).click();
  const draftId = await picker.inputValue();
  await page.getByLabel('Preset name', { exact: true }).fill('Draft switching check');
  await page.getByLabel('Base identity template').fill('Reversible draft instructions.');
  await picker.selectOption(originalId);
  await expect(page.getByLabel('Base identity template')).toHaveValue(originalText);
  await picker.selectOption(draftId);
  await expect(page.getByLabel('Preset name', { exact: true })).toHaveValue(
    'Draft switching check',
  );
  await expect(page.getByLabel('Base identity template')).toHaveValue(
    'Reversible draft instructions.',
  );
  await page.getByLabel('Undo preset edit', { exact: true }).click();
  await expect(page.getByLabel('Base identity template')).toHaveValue(originalText);
  await page.getByLabel('Redo preset edit', { exact: true }).click();
  await expect(page.getByLabel('Base identity template')).toHaveValue(
    'Reversible draft instructions.',
  );
  await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
  await expect(picker).toHaveValue(originalId);
  await expect(page.getByRole('button', { name: 'Save preset', exact: true })).toBeDisabled();
});

test('conversation menus organize history and fork an independent earlier turn', async ({
  page,
}) => {
  await openWorkspace(page);
  await startTask(page, 'History first turn');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await startTask(page, 'History second turn');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.conversation-turn')).toHaveCount(2);

  const actions = () =>
    page
      .locator('.session-row[data-selected="true"]')
      .getByRole('button', { name: /Conversation actions/ });
  await actions().click();
  await page.getByRole('menuitem', { name: 'Rename conversation', exact: true }).click();
  await page.getByLabel('Conversation name', { exact: true }).fill('History workflow');
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.locator('.topbar h1')).toHaveText('History workflow');
  await page.getByRole('button', { name: 'New folder', exact: true }).click();
  await page.getByLabel('Folder name', { exact: true }).fill('Browser investigations');
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await expect(
    page.getByRole('button', { name: /Folder actions for Browser investigations/ }),
  ).toBeVisible();
  await actions().click();
  await page.getByRole('menuitem', { name: 'Move to folder', exact: true }).click();
  await page
    .getByLabel('Folder', { exact: true })
    .selectOption({ label: 'Browser investigations' });
  await page.getByRole('dialog').getByRole('button', { name: 'Move', exact: true }).click();
  await actions().click();
  await page.getByRole('menuitem', { name: 'Fork conversation', exact: true }).click();
  await page.getByLabel('Fork after turn', { exact: true }).selectOption({ index: 0 });
  await page.getByRole('button', { name: 'Create fork', exact: true }).click();
  await expect(page.locator('.topbar h1')).toHaveText('History workflow fork');
  await expect(page.locator('.conversation-turn')).toHaveCount(1);
  await expect(page.getByText('Copied history', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Conversation workspace')).not.toContainText('History second turn');
  await startTask(page, 'Continue forked history');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.conversation-turn')).toHaveCount(2);
  await actions().click();
  await page.getByRole('menuitem', { name: 'Duplicate conversation', exact: true }).click();
  await expect(page.locator('.topbar h1')).toHaveText('History workflow fork copy');
  await expect(page.locator('.conversation-turn')).toHaveCount(2);
  await actions().click();
  await page.getByRole('menuitem', { name: 'Archive conversation', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Restore conversation', exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel('Task', { exact: true })).not.toBeAttached();
  await page.getByRole('button', { name: 'Archived', exact: true }).click();
  await expect(actions()).toBeVisible();
  await actions().click();
  await page.getByRole('menuitem', { name: 'Restore conversation', exact: true }).click();
  await expect(page.getByLabel('Task', { exact: true })).toBeVisible();
  await actions().click();
  await page.getByRole('menuitem', { name: 'Delete conversation', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByRole('dialog')).not.toBeAttached();
  await expect(
    page.getByRole('button', { name: 'History workflow fork copy', exact: true }),
  ).not.toBeAttached();
  await page.getByRole('button', { name: 'History workflow', exact: true }).click();
  await expect(page.locator('.conversation-turn')).toHaveCount(2);
  await page.getByLabel('Folder actions for Browser investigations', { exact: true }).click();
  await page.getByRole('menuitem', { name: 'Rename folder', exact: true }).click();
  await page.getByLabel('Folder name', { exact: true }).fill('Renamed investigations');
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByLabel('Folder actions for Renamed investigations', { exact: true }).click();
  await page.getByRole('menuitem', { name: 'Delete folder', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByRole('button', { name: 'History workflow', exact: true })).toBeVisible();
  await expect(page.locator('.conversation-turn')).toHaveCount(2);
  await page.reload();
  await expect(page.locator('.topbar h1')).toHaveText('History workflow');
  await expect(page.locator('.conversation-turn')).toHaveCount(2);
  await page.screenshot({ path: 'test-results/conversation-history.png', fullPage: true });
});
