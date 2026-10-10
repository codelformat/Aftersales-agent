import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const baseURL = 'http://127.0.0.1:4174/Aftersales-agent/';

export default defineConfig({
  testDir: fileURLToPath(new URL('./e2e/', import.meta.url)),
  outputDir: fileURLToPath(new URL('./test-results/', import.meta.url)),
  forbidOnly: !!process.env.CI,
  workers: process.env.CI ? 1 : undefined,
  retries: 0,
  use: {
    baseURL,
    viewport: { width: 1440, height: 900 },
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: {
    command: 'npm run build:replay && npx vite preview --mode replay --port 4174 --strictPort',
    cwd: import.meta.dirname,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
