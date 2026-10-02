import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';

/** Enter a fresh conversation through the same access boundary as a local user. */
async function openWorkspace(page: Page) {
  await page.goto('/');
  await page.getByLabel('Local access token').fill('playwright-local-test-token');
  await page.getByRole('button', { name: 'Open workspace' }).click();
  await expect(page.getByLabel('Conversation workspace')).toBeAttached();
  if (!(await page.getByRole('button', { name: 'New conversation', exact: true }).isVisible()))
    await page.getByRole('button', { name: 'Toggle sidebar', exact: true }).click();
  await page.getByRole('button', { name: 'New conversation', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Preview', exact: true })).toBeDisabled();
}

/** Require the visible preview before requesting model execution. */
async function startTask(page: Page, input: string) {
  await page.getByLabel('Task', { exact: true }).fill(input);
  await page.getByRole('button', { name: /^(Save & preview|Preview)$/ }).click();
  await expect(page.getByText('Frozen', { exact: true })).toBeVisible();
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
  await startTask(page, '/read README.md');
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
  await expect(page.getByText('Settings saved. Create a new preview to use them.')).toBeVisible();
  await page.getByLabel('Close settings').click();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('local-contract-model');
  await page.getByRole('combobox', { name: 'Provider', exact: true }).selectOption('fake');
  await page.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(page.getByText('Settings saved. Create a new preview to use them.')).toBeVisible();
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
  await expect(page.getByText('Settings saved. Create a new preview to use them.')).toBeVisible();
  await page.getByLabel('Close settings').click();
  await startTask(page, `/tool ${name} {"text":"browser MCP evidence"}`);
  await page.getByRole('button', { name: 'Approve once' }).click();
  await expect(page.getByTestId('run-status')).toHaveText('succeeded');
  await expect(page.locator('.markdown')).toContainText('MCP evidence: browser MCP evidence');
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
