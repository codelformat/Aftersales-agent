import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

async function mountApp(hash = '#/', mode: 'live' | 'replay' = 'live') {
  window.history.replaceState(null, '', hash);
  vi.stubEnv('VITE_DATA_SOURCE', mode);
  vi.resetModules();
  const { default: App } = await import('./App');
  return render(<App />);
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllEnvs());

describe('hash routes', () => {
  it.each([
    ['#/', '客服工作台'],
    ['#/desk', '客服工作台'],
    ['#/ops/review', '待审队列'],
    ['#/ops/evals', '评估趋势'],
    ['#/ops/faith', '编造台账'],
    ['#/ops/tools', '工具审计'],
    ['#/theater', '场景库'],
    ['#/theater/after-sales', '场景播放器'],
  ])('renders %s as %s', async (hash, title) => {
    await mountApp(hash);
    expect(screen.getByRole('heading', { name: title, level: 1 })).toBeInTheDocument();
  });

  it('navigates through the three top bar entries', async () => {
    const user = userEvent.setup();
    await mountApp();
    expect(screen.getByText('示例商城客服')).toBeInTheDocument();
    for (const [entry, title, hash] of [
      ['运营台', '待审队列', '#/ops/review'],
      ['回放剧场', '场景库', '#/theater'],
      ['工作台', '客服工作台', '#/desk'],
    ]) {
      await user.click(screen.getByRole('link', { name: entry }));
      expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument();
      expect(window.location.hash).toBe(hash);
    }
  });
});

describe('replay mode', () => {
  it('redirects the home hash to the scene library', async () => {
    await mountApp('#/', 'replay');
    await waitFor(() => expect(window.location.hash).toBe('#/theater'));
    expect(await screen.findByRole('heading', { name: '场景库' })).toBeInTheDocument();
  });

  it('shows the recording banner with the local run link', async () => {
    await mountApp('#/theater/after-sales', 'replay');
    expect(screen.getByText(/这是录制的真实会话回放/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '在本地运行完整系统 →' }))
      .toHaveAttribute('href', 'https://github.com/codelformat/Aftersales-agent#run-locally');
    expect(screen.getByRole('heading', { name: '场景播放器' })).toBeInTheDocument();
    expect(window.location.hash).toBe('#/theater/after-sales');
  });

  it('does not show the recording banner in live mode', async () => {
    await mountApp();
    expect(screen.getByRole('heading', { name: '客服工作台' })).toBeInTheDocument();
    expect(screen.queryByText(/这是录制的真实会话回放/)).not.toBeInTheDocument();
  });
});

describe('view mode', () => {
  it('defaults to engineering view', async () => {
    await mountApp();
    expect(screen.getByRole('radio', { name: '工程视角' })).toHaveAttribute('aria-checked', 'true');
  });

  it('persists the selection and restores it after remounting', async () => {
    const user = userEvent.setup();
    const app = await mountApp();
    await user.click(screen.getByRole('radio', { name: '客户视角' }));
    expect(localStorage.getItem('view_mode')).toBe('customer');
    app.unmount();
    await mountApp();
    expect(screen.getByRole('radio', { name: '客户视角' })).toHaveAttribute('aria-checked', 'true');
  });

  it('keeps the selected view when its toggle is clicked again', async () => {
    const user = userEvent.setup();
    await mountApp();
    await user.click(screen.getByRole('radio', { name: '工程视角' }));
    expect(screen.getByRole('radio', { name: '工程视角' })).toHaveAttribute('aria-checked', 'true');
  });

  it('falls back to engineering view for an invalid stored value', async () => {
    localStorage.setItem('view_mode', 'invalid');
    await mountApp();
    expect(screen.getByRole('radio', { name: '工程视角' })).toHaveAttribute('aria-checked', 'true');
  });

  it('shows the switch on a theater playback page', async () => {
    await mountApp('#/theater/after-sales');
    expect(screen.getByRole('radiogroup', { name: '视角切换' })).toBeInTheDocument();
  });

  it.each(['#/ops/review', '#/ops/evals', '#/ops/faith', '#/ops/tools', '#/theater'])(
    'hides the switch on %s', async (hash) => {
      await mountApp(hash);
      expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
      expect(screen.queryByRole('radiogroup', { name: '视角切换' })).not.toBeInTheDocument();
    },
  );
});
