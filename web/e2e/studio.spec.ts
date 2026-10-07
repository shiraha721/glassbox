import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';

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

test('retrying a lost Run response reuses its body after history revision advances', async ({
  page,
}) => {
  await openWorkspace(page);
  let loseResponse = true;
  await page.route('**/api/v1/runs', async (route) => {
    if (route.request().method() !== 'POST' || !loseResponse) return route.continue();
    loseResponse = false;
    const accepted = await route.fetch();
    expect(accepted.status()).toBe(202);
    await route.abort('failed');
  });
  await startTask(page, 'A task whose accepted response is lost.');
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeEnabled();
  const sessionId = await page.evaluate(() => localStorage.getItem('studio.session'));
  await expect
    .poll(
      async () => (await (await page.request.get(`/api/v1/sessions/${sessionId}`)).json()).revision,
    )
    .toBe(1);
  // Simulate a normal focus refresh discovering the completed Run and its newer history revision.
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')));
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.getByLabel('Task', { exact: true })).toHaveValue('');
  const runs = await (await page.request.get(`/api/v1/sessions/${sessionId}/runs`)).json();
  expect(runs).toHaveLength(1);
  await expect(page.locator('.conversation-turn')).toHaveCount(1);
});

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
  // Workspace access is chosen per run beside the model; the Tools panel reflects it.
  await page.getByRole('button', { name: 'Workspace access', exact: true }).click();
  await page.getByRole('button', { name: /^Read only/ }).click();
  await page.getByRole('button', { name: 'Tools', exact: true }).click();
  const catalog = page.getByLabel('Tool catalog', { exact: true });
  await catalog.getByLabel('Search tools', { exact: true }).fill('apply_patch');
  await expect(catalog.locator('[data-tool="apply_patch"]')).toBeVisible();
  await expect(catalog.getByText('Blocked by read-only', { exact: true })).toBeVisible();
  await catalog.getByText('Arguments', { exact: true }).click();
  await expect(catalog.getByText('"expected_hashes"', { exact: false })).toBeVisible();
  await catalog.getByRole('button', { name: 'Edit permissions', exact: true }).click();
  const settings = page.getByRole('dialog', { name: 'Settings' });
  await settings.getByLabel('Search tools', { exact: true }).fill('apply_patch');
  await settings
    .getByRole('combobox', { name: 'apply_patch permission', exact: true })
    .selectOption('ask');
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
          access: 'read-only',
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
      data: { ...state.config, tools: { ...state.config.tools, apply_patch: 'allow' } },
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
  await page.route('**/api/v1/desktop/folder-picker', (route) =>
    route.fulfill({
      json: { status: 'unavailable', path: null, detail: 'No desktop in this browser fixture.' },
    }),
  );
  await page.getByRole('button', { name: 'Choose folder', exact: true }).click();
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

test('folder selection preserves cancellation, falls back when unavailable and keeps project drafts separate', async ({
  page,
}) => {
  await openWorkspace(page);
  const boot = await (await page.request.get('/api/v1/bootstrap')).json();
  const root = path.join(path.dirname(boot.workspace_root), 'native-ui-project');
  mkdirSync(root, { recursive: true });
  writeFileSync(path.join(root, 'README.md'), 'Separate native-selected project.');
  let outcome: 'selected' | 'cancelled' | 'unavailable' = 'cancelled';
  await page.route('**/api/v1/desktop/folder-picker', (route) =>
    route.fulfill({
      json: {
        status: outcome,
        path: outcome === 'selected' ? root : null,
        detail: outcome === 'unavailable' ? 'Desktop unavailable.' : null,
      },
    }),
  );
  const add = page.getByRole('button', { name: 'Add project', exact: true });
  await add.click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Choose folder', exact: true }).click();
  await expect(dialog.getByText('Folder selection cancelled.', { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole('textbox', { name: 'Workspace folder', exact: true }),
  ).not.toBeAttached();
  outcome = 'selected';
  await dialog.getByRole('button', { name: 'Choose folder', exact: true }).click();
  await expect(dialog.locator('output')).toHaveText(root);
  await expect(dialog.getByLabel('Project name', { exact: true })).toHaveValue('native-ui-project');
  outcome = 'cancelled';
  await dialog.getByRole('button', { name: 'Change folder', exact: true }).click();
  await expect(dialog.locator('output')).toHaveText(root);
  await dialog.getByRole('button', { name: 'Add project', exact: true }).click();
  await startTask(page, '/read README.md');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await page.getByRole('button', { name: 'Default workspace', exact: true }).click();
  await expect(page.locator('.conversation-turn')).toHaveCount(0);
  await page.getByRole('button', { name: 'native-ui-project', exact: true }).click();
  await expect(page.locator('.conversation-turn')).toHaveCount(0);
  await startTask(page, 'New independent project draft.');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  const after = await (await page.request.get('/api/v1/bootstrap')).json();
  const project = after.projects.find(
    (item: { workspace_root: string }) => item.workspace_root === root,
  );
  expect(
    after.sessions.filter((item: { project_id: string }) => item.project_id === project.id),
  ).toHaveLength(2);
  await add.click();
  outcome = 'unavailable';
  await dialog.getByRole('button', { name: 'Choose folder', exact: true }).click();
  await expect(dialog.getByText('Desktop unavailable.', { exact: true })).toBeVisible();
  await dialog.getByLabel('Workspace folder', { exact: true }).fill(root);
  await dialog.getByRole('button', { name: 'Add project', exact: true }).focus();
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(
    true,
  );
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeAttached();
  await expect(add).toBeFocused();
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
  await expect(page.locator('.conversation-turn')).toHaveCount(1);
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
  await expect(page.locator('.tool-step')).toHaveCount(1);
  await expect(page.locator('.tool-step')).toContainText('done');
  await expect(page.locator('.conversation-turn')).toHaveCount(1);
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

test('providers and models feed the model picker, and keys are write-only', async ({ page }) => {
  await openWorkspace(page);
  await page.getByLabel('Task', { exact: true }).fill('configuration draft');
  await page.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const settings = page.getByRole('dialog', { name: 'Settings' });
  const provider = settings.getByRole('region', { name: 'Provider OpenAI-compatible' });
  await provider.getByLabel('Base URL', { exact: true }).fill('http://127.0.0.1:11434/v1');
  const pages = settings.getByRole('navigation', { name: 'Settings pages' });
  await pages.getByRole('button', { name: 'Models', exact: true }).click();
  await settings.getByLabel('Add model to OpenAI-compatible').fill('local-contract-model');
  await settings.getByRole('button', { name: 'Add', exact: true }).click();
  await settings.getByText('Prices · US$ per million tokens').click();
  await settings.getByLabel('Input price for local-contract-model').fill('2.5');
  await settings.getByLabel('Output price for local-contract-model').fill('10');
  await settings.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(settings.getByText('Settings saved. New runs use these settings.')).toBeVisible();
  const saved = (await (await page.request.get('/api/v1/bootstrap')).json()).config;
  expect(saved.providers[0].prices['local-contract-model']).toEqual({
    input: 2.5,
    output: 10,
    cache_read: null,
    cache_write: null,
  });
  await settings.getByLabel('Close settings').click();
  // The picker lists the saved model; the live preview compiles with the chosen model.
  await page.getByRole('button', { name: 'Model', exact: true }).click();
  await page.getByRole('button', { name: 'local-contract-model', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Model', exact: true })).toContainText(
    'local-contract-model',
  );
  await page.getByText('Prepared request body', { exact: true }).click();
  await expect(page.getByLabel('Request inspector')).toContainText('"local-contract-model"');
  // A key is sent once and only its source and last characters come back.
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await settings.getByLabel('API key', { exact: true }).fill('sk-e2e-0123456789');
  await settings.getByRole('button', { name: 'Save key', exact: true }).click();
  await expect(settings.getByText('Saved in the studio · ends in 6789')).toBeVisible();
  await expect(settings.getByLabel('API key', { exact: true })).toHaveValue('');
  await settings.getByRole('button', { name: 'Remove saved key', exact: true }).click();
  await expect(settings.getByText('Saved in the studio · ends in 6789')).not.toBeAttached();
  await settings.getByLabel('Close settings').click();
  await page.getByRole('button', { name: 'Model', exact: true }).click();
  await page.getByRole('button', { name: 'Simulated model', exact: true }).click();
});

test('discover a stdio MCP tool, authorize it, and complete the tool loop', async ({ page }) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page
    .getByRole('navigation', { name: 'Settings pages' })
    .getByRole('button', { name: 'MCP servers', exact: true })
    .click();
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

test('workspace context refreshes after terminal polling when SSE never connects', async ({
  page,
}) => {
  await openWorkspace(page);
  const bootstrap = await (await page.request.get('/api/v1/bootstrap')).json();
  const shell = bootstrap.tools.some((tool: { name: string }) => tool.name === 'powershell')
    ? 'powershell'
    : 'bash';
  bootstrap.config.tools[shell] = 'allow';
  expect((await page.request.put('/api/v1/config', { data: bootstrap.config })).ok()).toBe(true);
  await page.reload();
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await expect(page.getByLabel('Project memory', { exact: true })).toBeVisible();
  const previousMemory = await page.getByLabel('Project memory', { exact: true }).inputValue();
  await page.route('**/api/v1/runs/*/events', (route) => route.abort());
  // Delay the real file effect until the ordinary post-submission refresh has completed.
  const command =
    shell === 'powershell'
      ? "Start-Sleep -Milliseconds 1200; New-Item -ItemType Directory -Force -Path (Join-Path $env:RTAGENT_WORKSPACE '.rtagent') | Out-Null; [IO.File]::WriteAllText((Join-Path $env:RTAGENT_WORKSPACE '.rtagent/MEMORY.md'), 'Terminal poll memory', [Text.UTF8Encoding]::new($false))"
      : "sleep 1.2; mkdir -p .rtagent; printf 'Terminal poll memory' > .rtagent/MEMORY.md";
  await startTask(page, `/tool ${shell} ${JSON.stringify({ command })}`);
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await expect(page.getByLabel('Project memory', { exact: true })).toHaveValue(previousMemory);
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.getByLabel('Project memory', { exact: true })).toHaveValue(
    'Terminal poll memory',
  );
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

test('global skills from Settings are available in every project', async ({ page }) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const settings = page.getByRole('dialog', { name: 'Settings' });
  await settings
    .getByRole('navigation', { name: 'Settings pages' })
    .getByRole('button', { name: 'Skills', exact: true })
    .click();
  await settings.getByRole('button', { name: 'New skill', exact: true }).click();
  await settings.getByLabel('Skill name', { exact: true }).fill('browser-global');
  await settings.getByLabel('Description', { exact: true }).fill('A shared browser check.');
  await settings.getByLabel('Instructions', { exact: true }).fill('Report the shared rule.');
  await settings.getByRole('button', { name: 'Save skill', exact: true }).click();
  await expect(settings.getByRole('button', { name: /browser-global/ })).toContainText('global');
  await settings.getByLabel('Close settings').click();
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await page.getByRole('button', { name: 'Skills', exact: true }).click();
  await expect(page.getByLabel('Use browser-global', { exact: true })).toBeVisible();
  const skills = await (await page.request.get('/api/v1/skills')).json();
  const saved = skills.skills.find((item: { name: string }) => item.name === 'browser-global');
  const removed = await page.request.delete(
    `/api/v1/skills/browser-global?expected_hash=${saved.hash}`,
  );
  expect(removed.status()).toBe(204);
});

test('a typed project folder is created only when requested', async ({ page }) => {
  await openWorkspace(page);
  const boot = await (await page.request.get('/api/v1/bootstrap')).json();
  const root = path.join(path.dirname(boot.workspace_root), `created-${Date.now()}`, 'app');
  await page.getByRole('button', { name: 'Add project', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Enter a path', exact: true }).click();
  await dialog.getByLabel('Workspace folder', { exact: true }).fill(root);
  await dialog.getByLabel('Project name', { exact: true }).fill('Created app');
  await dialog.getByRole('button', { name: 'Add project', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('does not exist');
  expect(existsSync(root)).toBe(false);
  await dialog.getByLabel('Create the folder if it does not exist', { exact: true }).check();
  await dialog.getByRole('button', { name: 'Add project', exact: true }).click();
  await expect(dialog).not.toBeAttached();
  await expect(page.getByRole('button', { name: 'Created app', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(existsSync(root)).toBe(true);
});

test('conversations start under the workspace and context reports live request usage', async ({
  page,
}) => {
  await openWorkspace(page);
  // New conversation lives inside the Conversations section, which names the active workspace.
  const conversations = page.locator('.history-controls');
  await expect(conversations.getByText('Conversations', { exact: true })).toBeVisible();
  await expect(conversations.getByText('Default workspace', { exact: true })).toBeVisible();
  await expect(
    conversations.getByRole('button', { name: 'New conversation', exact: true }),
  ).toBeVisible();

  await startTask(page, '/read README.md');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await page.getByRole('button', { name: 'Context', exact: true }).click();
  // The simulated provider reports no tokens; the panel says so rather than inventing numbers.
  await expect(page.getByLabel('Conversation context')).toContainText('reported no token usage');
  await expect(page.getByTestId('conversation-cost')).toHaveText(
    'Spent in this conversation: $0.0000 · 1 run without a price or reported usage.',
  );

  // A provider that does report usage fills the window bar, cache-hit rate and output metrics.
  await page.route('**/api/v1/sessions/*/context', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.context_window = 200000;
    body.live_usage = {
      model: 'test-model',
      prompt_tokens: 50000,
      input_tokens: 5000,
      cache_read_tokens: 45000,
      cache_write_tokens: 0,
      output_tokens: 800,
    };
    await route.fulfill({ response, json: body });
  });
  await page.getByRole('button', { name: 'Close context', exact: true }).click();
  await page.getByRole('button', { name: 'Context', exact: true }).click();
  const usage = page.locator('.context-usage');
  await expect(usage).toContainText('50,000 / 200,000 tokens');
  await expect(usage).toContainText('25%'); // window used
  await expect(usage).toContainText('90%'); // cache hit rate
  await expect(usage.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
});

test('an evaluation compares prompt variants on the same cases', async ({ page }) => {
  page.on('dialog', (dialog) => void dialog.accept());
  await page.goto('/');
  await page.getByRole('button', { name: 'Evaluations', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Evaluations' });
  await dialog.getByLabel('Evaluation name').fill('Browser eval');
  await dialog.getByLabel('Task').fill('/read README.md');
  await dialog.getByLabel('Text', { exact: true }).fill('browser test workspace');
  await dialog.getByRole('button', { name: 'Add case' }).click();
  await dialog.getByLabel('Task').nth(1).fill('Summarize this project.');
  await dialog.getByLabel('Text', { exact: true }).nth(1).fill('browser test workspace');
  await dialog.getByLabel('Workspace access').nth(1).selectOption('read-only');

  // Running saves the definition first, then runs every case under every variant.
  await dialog.getByRole('button', { name: 'Run comparison', exact: true }).click();
  await expect(dialog.getByTestId('batch-status')).toHaveText('succeeded', { timeout: 20_000 });
  await expect(dialog.getByText('4 of 4 runs')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Case 1 under Variant A: pass' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Case 2 under Variant B: fail' })).toBeVisible();
  const summary = dialog.locator('.eval-table').first();
  await expect(summary.getByRole('row', { name: /Variant A/ })).toContainText('50%');

  await dialog.getByRole('button', { name: 'Case 1 under Variant A: pass' }).click();
  await expect(dialog.getByLabel('Result detail')).toContainText('browser test workspace');
  await expect(
    dialog.getByRole('button', { name: /Browser eval.*2 cases · 2 variants · succeeded/ }),
  ).toBeVisible();

  // Eval Runs live in hidden sessions, not in the conversation list.
  await dialog.getByRole('button', { name: 'Close evaluations', exact: true }).click();
  await expect(page.getByLabel('Conversation history')).not.toContainText('Variant A · Case 1');
});

test('a run that ends while the tab is in the background raises a notification', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const state = window as unknown as {
      shown: { title: string; body?: string }[];
      backgrounded: boolean;
      Notification: unknown;
    };
    state.shown = [];
    state.backgrounded = false;
    state.Notification = class {
      static permission = 'granted';
      static requestPermission = async () => 'granted';
      onclick: unknown = null;
      constructor(title: string, options?: { body?: string }) {
        state.shown.push({ title, body: options?.body });
      }
      close() {}
    };
    Object.defineProperty(document, 'hidden', { get: () => state.backgrounded });
  });
  await openWorkspace(page);
  const toggle = page.getByRole('button', { name: 'Notifications', exact: true });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  // A foreground run is visible already, so it does not notify.
  await startTask(page, '/read README.md');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await page.evaluate(() => {
    (window as unknown as { backgrounded: boolean }).backgrounded = true;
  });
  await startTask(page, 'Summarize the workspace.');
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { shown: unknown[] }).shown))
    .toEqual([
      { title: 'Run finished', body: expect.stringContaining('Summarize the workspace.') },
    ]);
});

test('approving for the conversation covers later calls until revoked', async ({ page }) => {
  await openWorkspace(page);
  await startTask(page, '/echo first standing approval');
  await page.getByRole('button', { name: 'Allow echo in this conversation', exact: true }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  // The next echo runs without a prompt.
  await startTask(page, '/echo second standing approval');
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.conversation-turn')).toHaveCount(2);
  // Revoking from the Tools panel makes the tool ask again.
  await page.getByRole('button', { name: 'Tools', exact: true }).click();
  const grants = page.getByRole('region', { name: 'Allowed in this conversation' });
  await grants.getByRole('button', { name: 'Revoke echo', exact: true }).click();
  await expect(grants).not.toBeAttached();
  await page.getByRole('button', { name: 'Close tools', exact: true }).click();
  await startTask(page, '/echo third standing approval');
  await expect(page.getByRole('button', { name: 'Approve once', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Reject', exact: true }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');

  // Command rules are edited with the other tool permissions.
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const settings = page.getByRole('dialog', { name: 'Settings' });
  await settings
    .getByRole('navigation', { name: 'Settings pages' })
    .getByRole('button', { name: 'Tools', exact: true })
    .click();
  await settings.getByLabel('New command prefix').fill('git   status');
  await settings.getByRole('button', { name: 'Add rule', exact: true }).click();
  await settings.getByLabel('Permission for git status').selectOption('ask');
  await settings.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(settings.getByText('Settings saved. New runs use these settings.')).toBeVisible();
  const saved = (await (await page.request.get('/api/v1/bootstrap')).json()).config;
  expect(saved.command_rules).toEqual([{ prefix: 'git status', permission: 'ask' }]);
  await settings.getByRole('button', { name: 'Remove rule git status', exact: true }).click();
  await settings.getByRole('button', { name: 'Save settings', exact: true }).click();
  await settings.getByLabel('Close settings').click();
});

test('automatic compaction summarizes older turns once enabled and can be undone', async ({
  page,
}) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const settings = page.getByRole('dialog', { name: 'Settings' });
  await settings
    .getByRole('navigation', { name: 'Settings pages' })
    .getByRole('button', { name: 'Context', exact: true })
    .click();
  await settings
    .getByLabel("Compact automatically when a conversation's history grows large")
    .check();
  await settings.getByLabel('History size that triggers it (estimated tokens)').fill('1000');
  await settings.getByLabel('Recent turns kept verbatim').selectOption('1');
  await settings.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(settings.getByText('Settings saved. New runs use these settings.')).toBeVisible();
  await settings.getByLabel('Close settings').click();
  try {
    await startTask(page, 'Earlier evidence to summarize. '.repeat(200));
    await expect(page.getByTestId('run-status')).toHaveText('succeeded');
    await startTask(page, 'Keep this recent turn.');
    await expect(page.getByTestId('run-status')).toHaveText('succeeded');
    await page.getByRole('button', { name: 'Context', exact: true }).click();
    const context = page.getByLabel('Conversation context', { exact: true });
    await expect(context.getByTestId('auto-compact-status')).toContainText(
      'Automatic compaction is on',
    );
    await expect(context.getByText('Active summary', { exact: true })).toBeVisible();
    await expect(context.locator('.context-file summary')).toContainText('Automatic · succeeded');
    // Both turns stay in the conversation; only the history sent to new runs changed.
    await expect(page.locator('.conversation-turn')).toHaveCount(2);
    await context.getByRole('button', { name: 'Restore full context' }).click();
    await expect(context.getByText('Active summary', { exact: true })).not.toBeAttached();
  } finally {
    // Later tests share this server's settings.
    const config = (await (await page.request.get('/api/v1/bootstrap')).json()).config;
    await page.request.put('/api/v1/config', {
      data: { ...config, auto_compact: { ...config.auto_compact, enabled: false } },
    });
  }
});

test('a run that wrote a file can undo it from its turn', async ({ page }) => {
  await openWorkspace(page);
  await startTask(
    page,
    '/tool write_text ' + JSON.stringify({ path: 'undo-e2e.txt', content: 'draft from a run' }),
  );
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  const files = page.locator('.run-files');
  await files.getByText('Changed 1 file', { exact: true }).click();
  await expect(files.getByRole('list', { name: 'Changed files' })).toContainText('undo-e2e.txt');
  await files.getByRole('button', { name: 'Undo file changes', exact: true }).click();
  await expect(files.getByRole('status')).toHaveText('Restored 1 file.');
  await expect(files.getByText('restored', { exact: true })).toBeVisible();
  await expect(
    files.getByRole('button', { name: 'Undo file changes', exact: true }),
  ).toBeDisabled();
});

test('a custom slash command is suggested, expanded and shown as typed', async ({ page }) => {
  const boot = await (await page.request.get('/api/v1/bootstrap')).json();
  const folder = path.join(boot.workspace_root, '.agents', 'commands');
  mkdirSync(folder, { recursive: true });
  writeFileSync(
    path.join(folder, 'e2e-review.md'),
    '---\ndescription: Review one file\nargument-hint: <path>\n---\nReview $1 and list risks.\n',
  );
  await openWorkspace(page);
  const task = page.getByLabel('Task', { exact: true });
  await task.fill('/e2e');
  const suggestions = page.getByRole('list', { name: 'Commands' });
  await expect(suggestions).toContainText('Review one file');
  await task.press('Tab');
  await expect(task).toHaveValue('/e2e-review ');
  await task.pressSequentially('README.md');
  await expect(suggestions).not.toBeAttached();
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  const turn = page.locator('.conversation-turn').last();
  await expect(turn.locator('.command-invocation')).toHaveText('/e2e-review README.md');
  await turn.getByText('Sent as', { exact: true }).click();
  await expect(turn.locator('.command-expansion')).toContainText(
    'Review README.md and list risks.',
  );
  // The simulated model reports the task it received: the expansion, not the typed command.
  await expect(turn.locator('.agent-activity')).toContainText('Task: Review README.md and');
});
