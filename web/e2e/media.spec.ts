import { copyFile, mkdir, readFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { test, expect } from './fixtures';
import { seek } from './playback';
import { gateCompletion } from './recording';
import type { SceneLine } from '../src/data/DataSource';

test.skip(process.env.UPDATE_MEDIA !== '1', 'Set UPDATE_MEDIA=1 to capture hero frames');
test.use({ viewport: { width: 1280, height: 1080 } });

test('captures hero frames with the first completed gate open', async ({ page }) => {
  test.setTimeout(300_000);
  const recording = await readFile(new URL('../public/replays/multi-turn.jsonl', import.meta.url), 'utf8');
  const lines: SceneLine[] = recording.trim().split(/\r?\n/).slice(1).map(line => JSON.parse(line));
  const gate = gateCompletion(lines, 1);
  await page.goto('./#/theater/multi-turn?t=0&view=eng&lang=en');
  const slider = page.getByRole('slider', { name: '播放进度' });
  await expect(slider).toBeVisible();
  await page.getByRole('checkbox', { name: '自动停留' }).check();
  const max = Number(await slider.getAttribute('max'));
  const directory = new URL('../test-results/hero-frames/', import.meta.url);
  await rm(directory, { recursive: true, force: true });
  await mkdir(directory, { recursive: true });
  const framePath = (index: number) => fileURLToPath(new URL(`frame-${String(index).padStart(4, '0')}.png`, directory));
  let index = 0;
  let opened = false;
  const gateButton = page.getByRole('button', { name: /置信度闸/ });
  const gateImage = page.getByRole('region', { name: '置信度闸详情' }).getByRole('img', { name: /置信度/ });
  const latestReply = page.getByRole('article', { name: /^客服回复 / }).last();
  for (let position = 0; ; position = Math.min(max, position + 400)) {
    await seek(slider, position);
    // Holds extend playback time; wait until the gate also exists in the current projection.
    if (!opened && position >= gate.position && await gateButton.count() > 0) {
      await page.getByRole('button', { name: `查看此轮 ${gate.turn}`, exact: true }).click();
      await gateButton.click();
      await expect(page.getByRole('region', { name: '置信度闸详情' })).toBeVisible();
      opened = true;
    }
    await page.evaluate(() => new Promise<void>(resolve => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    }));
    if (opened && await gateImage.isVisible()) await gateImage.scrollIntoViewIfNeeded();
    if (await latestReply.count() > 0) await latestReply.scrollIntoViewIfNeeded();
    await page.screenshot({ path: framePath(index++), animations: 'disabled' });
    if (position === max) break;
  }
  const lastFrame = framePath(index - 1);
  for (let hold = 0; hold < 6; hold++) await copyFile(lastFrame, framePath(index++));
});
