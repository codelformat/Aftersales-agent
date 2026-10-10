import { readdirSync, statSync } from 'node:fs';
import { test as base, expect } from '@playwright/test';

export const test = base.extend<{ forbiddenRequests: string[]; pageErrors: string[] }>({
  forbiddenRequests: async ({}, use) => { await use([]); },
  pageErrors: async ({}, use) => { await use([]); },
  page: async ({ page, baseURL, forbiddenRequests, pageErrors }, use) => {
    if (!baseURL) throw new Error('Replay tests require baseURL');
    const root = new URL('../dist-replay/', import.meta.url);
    const staticURLs = new Set(readdirSync(root, { recursive: true, encoding: 'utf8' }).filter(file =>
      statSync(new URL(file, root)).isFile(),
    ).map(file => new URL(file.split('/').map(encodeURIComponent).join('/'), baseURL).href));
    staticURLs.add(baseURL);
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      url.hash = '';
      // 只放行构建中确实存在的文件。查询参数、API 和其他域名均拦下。
      if (request.method() === 'GET' && staticURLs.has(url.href)) await route.continue();
      else {
        forbiddenRequests.push(`${request.method()} ${request.url()}`);
        await route.abort('blockedbyclient');
      }
    });
    await use(page);
  },
});

test.afterEach(async ({ page, forbiddenRequests, pageErrors }) => {
  await page.unrouteAll({ behavior: 'wait' });
  expect(forbiddenRequests, 'Replay must only request dist-replay static files').toEqual([]);
  expect(pageErrors, 'Uncaught browser errors').toEqual([]);
});

export { expect };
