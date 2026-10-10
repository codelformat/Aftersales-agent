import { test, expect } from './fixtures';

test('welcome opens English autoplay with social preview metadata', async ({ page }) => {
  await page.goto('./');
  await expect(page).toHaveURL(/#\/welcome$/);
  await expect(page.getByRole('heading', { name: 'An after-sales support agent that knows when not to answer.', level: 1 })).toBeVisible();
  await expect(page.locator('meta[property="og:image"]')).toHaveAttribute('content', 'https://codelformat.github.io/Aftersales-agent/og.png');
  await expect(page).toHaveTitle('Aftersales Agent · Replay');
  await page.getByRole('link', { name: 'Watch the knowledge flywheel (27 s)' }).click();
  await expect(page).toHaveURL(/#\/theater\/flywheel/);
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible();
  await expect.poll(async () => Number(await page.getByRole('slider', { name: '播放进度' }).inputValue())).toBeGreaterThan(0);
  await expect(page.locator('p[aria-live="polite"]')).toHaveAttribute('lang', 'en');
});

test('welcome fits a 375px viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto('./#/welcome');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});
