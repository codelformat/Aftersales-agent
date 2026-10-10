import { test, expect } from './fixtures';
import { playToEnd, seek } from './playback';

test('场景库显示七张卡片', async ({ page }) => {
  await page.goto('./#/theater');
  await expect(page.getByRole('heading', { name: '场景库', exact: true })).toBeVisible();
  await expect(page.getByRole('article')).toHaveCount(7);
  await expect(page.getByRole('link', { name: /^观看 / })).toHaveCount(7);
});

test('flywheel 播放、拖到末尾后显示通过与带引用的回复', async ({ page }) => {
  await page.goto('./#/theater');
  await page.getByRole('link', { name: '观看 知识飞轮：从兜底到答对' }).click();
  await playToEnd(page);
  const ops = page.getByRole('region', { name: '运营侧' });
  await expect(ops.getByText('通过', { exact: true })).toBeVisible();
  const reply = page.getByRole('article', { name: '客服回复 2', exact: true });
  await expect(reply).toContainText('L2');
  await expect(reply.getByRole('button', { name: '查看引用 1 的原文' })).toBeVisible();
  // 也验证闸已通过，避免仅凭运营状态误判播放结果。
  await page.getByRole('button', { name: /透视面板 · \d+ 个节点/ }).click();
  await page.getByRole('button', { name: /置信度闸/ }).click();
  await expect(page.getByRole('region', { name: '置信度闸详情' }).getByText('通过', { exact: true })).toBeVisible();
});

test('其余六个场景依次以 4× 播放并拖到末尾，无 pageerror', async ({ page, pageErrors }) => {
  for (const id of ['refund', 'multi-turn', 'ticket', 'mcp-timeout', 'boundaries', 'strategies']) {
    await test.step(id, async () => {
      await page.goto('./#/theater');
      await page.locator(`a[href="#/theater/${id}"]`).click();
      await playToEnd(page);
      expect(pageErrors, id).toEqual([]);
    });
  }
});

test('深链接恢复时间、客户视角与工程视角', async ({ page }) => {
  await page.goto('./#/theater/flywheel?t=2&view=cust');
  const slider = page.getByRole('slider', { name: '播放进度' });
  await expect(slider).toHaveValue('2000');
  await expect(page.getByRole('radio', { name: '客户视角', exact: true })).toBeChecked();
  await expect(page.getByRole('button', { name: /透视面板 ·/ })).toHaveCount(0);
  await expect(page.getByText('客户问 L2 是否支持语音控制。现有知识缺少这一项。', { exact: true })).toBeVisible();
  await page.goto('./#/theater/multi-turn?t=2&view=eng');
  await expect(slider).toHaveValue('2000');
  await expect(page.getByRole('radio', { name: '工程视角', exact: true })).toBeChecked();
  await expect(page.getByRole('complementary', { name: '透视面板' })).toBeVisible();
});


for (const scene of ['flywheel', 'refund', 'multi-turn', 'ticket', 'mcp-timeout', 'boundaries', 'strategies']) {
  test(`${scene} keeps narration and controls visible without document scrolling`, async ({ page }) => {
    await page.goto(`./#/theater/${scene}?t=999&view=eng`);
    const narration = page.getByLabel('场景解说');
    const controls = page.getByRole('region', { name: '回放控制' });
    await expect(controls).toBeVisible();
    await expect(page.getByRole('textbox', { name: '输入您的问题' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: '客服工作台' })).toHaveCount(0);
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
      const messages = customer.getByRole('log', { name: '对话消息' });
      const assertMessageHeight = async () => {
        const available = await messages.evaluate(log => {
          // The customer stage fills the lane below its "客户" heading.
          return log.parentElement!.parentElement!.parentElement!.getBoundingClientRect().height;
        });
        expect((await messages.boundingBox())!.height).toBeGreaterThanOrEqual(available * 0.6 - 1);
      };
      await assertMessageHeight();
      await expect(messages.getByRole('article', { name: '客服回复 2', exact: true })).toBeInViewport();
      const toggle = customer.getByRole('button', { name: /透视面板 · \d+ 个节点/ });
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await toggle.click();
      const xray = customer.getByLabel('透视面板', { exact: true });
      await expect(xray).toBeVisible();
      await assertMessageHeight();
      const scroll = await xray.evaluate(el => {
        el.scrollTop = el.scrollHeight;
        return { position: el.scrollTop, max: el.scrollHeight - el.clientHeight };
      });
      expect(scroll.max).toBeGreaterThan(0);
      expect(scroll.position).toBeGreaterThan(0);
      expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(900);
      expect((await xray.boundingBox())!.y + (await xray.boundingBox())!.height).toBeLessThanOrEqual(controlsBox.y);
      // Timeline seeks must follow the current last message even after manually scrolling up.
      await messages.evaluate(log => { log.scrollTop = 0; log.dispatchEvent(new Event('scroll')); });
      await page.getByRole('checkbox', { name: '自动停留' }).uncheck();
      await page.getByRole('button', { name: /^章节 2：/ }).click();
      await expect(messages.getByRole('article')).toHaveCount(1);
      await expect.poll(() => messages.evaluate(log => log.scrollHeight - log.clientHeight - log.scrollTop)).toBeLessThanOrEqual(1);
      await seek(page.getByRole('slider', { name: '播放进度' }));
      await expect(messages.getByRole('article')).toHaveCount(2);
      await expect.poll(() => messages.evaluate(log => log.scrollHeight - log.clientHeight - log.scrollTop)).toBeLessThanOrEqual(1);
    }
  });
}
