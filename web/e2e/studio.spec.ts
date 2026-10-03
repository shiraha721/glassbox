import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';

/** Open the local workspace directly and create a fresh conversation. */
async function openWorkspace(page: Page) {
  await page.goto('/');
  await expect(page.getByLabel('Conversation workspace')).toBeAttached();
  if (!(await page.getByRole('button', { name: 'New conversation', exact: true }).isVisible()))
    await page.getByRole('button', { name: 'Toggle sidebar', exact: true }).click();
  await page.getByRole('button', { name: 'New conversation', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Preview', exact: true })).toBeDisabled();
}

/** Submit tasks directly; preview is an optional inspection action. */
async function startTask(page: Page, input: string) {
  await page.getByLabel('Task', { exact: true }).fill(input);
  await page.getByRole('button', { name: 'Run', exact: true }).click();
}

test('edit, preview, real file tool, inspect trace, and refresh without another run', async ({
  page,
}) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Prompt', exact: true }).click();
  await page
    .getByLabel('Base identity template')
    .fill('Analyze only with recorded evidence. Browser contract marker.');
  await page.getByLabel('Task', { exact: true }).fill('/read README.md');
  await page.getByRole('button', { name: 'Save & preview', exact: true }).click();
  await expect(page.getByText('Frozen', { exact: true })).toBeVisible();
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
  await expect(page.getByRole('button', { name: 'Preview', exact: true })).toBeDisabled();
});

test('settings save real endpoint references and invalidate a prepared preview', async ({
  page,
}) => {
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
  await expect(page.getByText('Frozen', { exact: true })).toBeVisible();
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
