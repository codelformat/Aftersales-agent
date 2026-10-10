import type { Locator, Page } from '@playwright/test';
import { expect } from './fixtures';

export async function seek(slider: Locator, position?: number) {
  const max = Number(await slider.getAttribute('max'));
  const value = String(position ?? max);
  // 原生 setter 绕过 React 的值跟踪，再触发真实 range input 事件。
  await slider.evaluate((element, value) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    setter.call(element, value);
  }, value);
  await slider.dispatchEvent('input');
  await expect(slider).toHaveValue(value);
}

export async function playToEnd(page: Page) {
  const slider = page.getByRole('slider', { name: '播放进度' });
  await expect(slider).toBeVisible();
  await page.getByRole('combobox', { name: '播放速度' }).selectOption('4');
  await page.getByRole('checkbox', { name: '自动停留' }).uncheck();
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(0);
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await seek(slider);
  await expect(page.getByRole('button', { name: '播放', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '跳过等待' })).toBeDisabled();
}
