import { expect, test } from '@playwright/test';

for (const scene of ['flywheel', 'refund', 'multi-turn', 'ticket', 'mcp-timeout', 'boundaries', 'strategies']) {
  test(`${scene} keeps narration and controls visible without document scrolling`, async ({ page }) => {
    await page.goto(`#/theater/${scene}?t=999&view=eng`);
    const narration = page.getByLabel('场景解说');
    const controls = page.getByRole('region', { name: '回放控制' });
    await expect(controls).toBeVisible();
    const bounds = await page.evaluate(() => ({ height: innerHeight, document: document.documentElement.scrollHeight }));
    expect(bounds.document).toBeLessThanOrEqual(bounds.height);
    const narrationBox = (await narration.boundingBox())!;
    const controlsBox = (await controls.boundingBox())!;
    const topBarBox = (await page.locator('header').first().boundingBox())!;
    expect(narrationBox.y).toBeGreaterThanOrEqual(topBarBox.y + topBarBox.height);
    expect(await narration.evaluate(el => parseFloat(getComputedStyle(el).top))).toBe(topBarBox.y + topBarBox.height);
    expect(controlsBox.y + controlsBox.height).toBeLessThanOrEqual(900);
    expect(await narration.evaluate(el => getComputedStyle(el).position)).toBe('sticky');
    expect(await controls.evaluate(el => getComputedStyle(el).position)).toBe('sticky');
    expect(await controls.evaluate(el => getComputedStyle(el).bottom)).toBe('0px');
    if (scene === 'flywheel') {
      const customer = page.getByRole('region', { name: '客户侧' });
      const toggle = customer.getByRole('button', { name: /透视面板 · \d+ 个节点/ });
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await toggle.click();
      const xray = customer.getByLabel('透视面板', { exact: true });
      await expect(xray).toBeVisible();
      const scroll = await xray.evaluate(el => {
        el.scrollTop = el.scrollHeight;
        return { position: el.scrollTop, max: el.scrollHeight - el.clientHeight };
      });
      expect(scroll.max).toBeGreaterThan(0);
      expect(scroll.position).toBeGreaterThan(0);
      expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(900);
      expect((await xray.boundingBox())!.y + (await xray.boundingBox())!.height).toBeLessThanOrEqual(controlsBox.y);
    }
  });
}
