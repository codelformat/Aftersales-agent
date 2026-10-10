import { defineConfig } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export default defineConfig({
  testDir: './e2e',
  outputDir: join(tmpdir(), 'aftersales-theater-playwright'),
  use: { channel: process.env.PLAYWRIGHT_CHANNEL, baseURL: 'http://127.0.0.1:4173/Aftersales-agent/', viewport: { width: 1440, height: 900 } },
  webServer: {
    command: 'npm run dev -- --mode replay --host 127.0.0.1 --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173/Aftersales-agent/',
    reuseExistingServer: !process.env.CI,
  },
});
