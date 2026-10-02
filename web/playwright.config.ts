import { defineConfig } from '@playwright/test';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: {
    baseURL: 'http://127.0.0.1:8765',
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'uv run uvicorn rtagent.main:create_app --factory --host 127.0.0.1 --port 8765',
    cwd: root,
    url: 'http://127.0.0.1:8765/api/v1/health',
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      STUDIO_DATA_DIR: path.join(root, 'var', `e2e-${Date.now()}`),
      STUDIO_WORKSPACE_ROOT: path.join(root, 'tests', 'fixtures'),
      STUDIO_ACCESS_TOKEN: 'playwright-local-test-token',
      STUDIO_ALLOWED_ORIGINS: '["http://127.0.0.1:8765"]',
    },
  },
});
