import { mkdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import type { Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { seek } from './playback';
import { gateCompletion } from './recording';
import type { SceneLine } from '../src/data/DataSource';

test.use({ viewport: { width: 1440, height: 900 } });
test.skip(process.env.UPDATE_SCREENSHOTS !== '1', 'Set UPDATE_SCREENSHOTS=1 to write docs/media PNGs');

async function capture(page: Page, name: string) {
  const media = new URL('../../docs/media/', import.meta.url);
  await mkdir(media, { recursive: true });
  await page.evaluate(async () => { await document.fonts.ready; });
  await page.screenshot({ path: fileURLToPath(new URL(`${name}.png`, media)), animations: 'disabled' });
}

async function openGate(page: Page, occurrence: number) {
  const recording = await readFile(new URL('../public/replays/multi-turn.jsonl', import.meta.url), 'utf8');
  const lines: SceneLine[] = recording.trim().split(/\r?\n/).slice(1).map(line => JSON.parse(line));
  const { position, turn } = gateCompletion(lines, occurrence);
  await page.goto('./#/theater/multi-turn?t=0&view=eng');
  const slider = page.getByRole('slider', { name: '播放进度' });
  await expect(slider).toBeVisible();
  await page.getByRole('checkbox', { name: '自动停留' }).uncheck();
  await seek(slider, position);
  // done may share its timestamp with the next chat, so select the gate's turn explicitly.
  await page.getByRole('button', { name: `查看此轮 ${turn}`, exact: true }).click();
  await page.getByRole('button', { name: /置信度闸/ }).click();
  const gate = page.getByRole('region', { name: '置信度闸详情' });
  await expect(gate.getByRole('img', { name: /置信度/ })).toBeVisible();
  await expect(gate.getByText(occurrence === 1 ? '通过' : '拦下', { exact: true })).toBeVisible();
  await gate.scrollIntoViewIfNeeded();
}

test('gallery', async ({ page }) => {
  await page.goto('./#/theater');
  await expect(page.getByRole('link', { name: /^观看 / })).toHaveCount(7);
  await capture(page, 'gallery');
});

for (const name of ['desk-xray', 'gate']) {
  test(name, async ({ page }) => {
    // 工作台展示第一轮通过；闸截图展示第二轮证据不足。
    await openGate(page, name === 'desk-xray' ? 1 : 2);
    await capture(page, name);
  });
}

test('flywheel-split', async ({ page }) => {
  await page.goto('./#/theater/flywheel?t=0&view=eng');
  const slider = page.getByRole('slider', { name: '播放进度' });
  await expect(slider).toBeVisible();
  await seek(slider);
  await expect(page.getByRole('region', { name: '运营侧' }).getByText('通过', { exact: true })).toBeVisible();
  await expect(page.getByRole('article', { name: '客服回复 2' }).getByRole('button', { name: '查看引用 1 的原文' })).toBeVisible();
  await capture(page, 'flywheel-split');
});

test('strategies', async ({ page }) => {
  await page.goto('./#/theater/strategies');
  await expect(page.getByRole('table', { name: '四策略指标对比' }).getByRole('row')).toHaveCount(5);
  await capture(page, 'strategies');
});

test('ops-review', async ({ page }) => {
  await page.goto('./#/ops/review');
  const table = page.getByRole('table', { name: '待审问题列表' });
  const expand = table.getByRole('button', { expanded: false }).first();
  await expect(expand).toBeVisible();
  await expand.click();
  await expect(table.getByRole('heading', { name: '用户原话' }).first()).toBeVisible();
  await capture(page, 'ops-review');
});
