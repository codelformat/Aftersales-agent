import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ViewModeProvider, useViewMode } from '../app/ViewModeContext';
import PlayerPage from './PlayerPage';
import GalleryPage from './GalleryPage';
import App from '../app/App';
import { fixture, recording } from './test-fixtures';
import { gateCompletion } from '../../e2e/recording';

function ViewSwitch() {
  const { setViewMode } = useViewMode();
  return <button onClick={() => setViewMode('eng')}>切换工程</button>;
}
function mount(sceneId: string, query = '') {
  window.history.replaceState(null, '', `#/theater/${sceneId}${query}`);
  return render(<ViewModeProvider><ViewSwitch /><PlayerPage sceneId={sceneId} /></ViewModeProvider>);
}
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^\/Aftersales-agent/, '');
    const data = fixture(path);
    return data === undefined ? new Response('', { status: 404 }) : new Response(data);
  }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
describe('gallery', () => {
  it('shows seven scene cards with proof, duration and tags', () => {
    render(<GalleryPage />);
    const links = screen.getAllByRole('link', { name: /观看/ });
    expect(links).toHaveLength(7);
    expect(links[0]).toHaveAttribute('href', '#/theater/flywheel');
    expect(screen.getByText(/8.9 s/)).toBeInTheDocument();
    expect(screen.getByText('人工核准的知识可用于下一次回答。')).toBeInTheDocument();
  });
});
describe('player', () => {
  it.each([
    [1, '通过', 1], [2, '拦下', 3],
  ] as const)('opens the completed gate #%s using event time and explicit turn selection', async (occurrence, status, visibleTurns) => {
    const target = gateCompletion(recording('multi-turn').lines, occurrence);
    mount('multi-turn', '?t=0&view=eng');
    const slider = await screen.findByRole('slider', { name: '播放进度' });
    fireEvent.click(screen.getByRole('checkbox', { name: '自动停留' }));
    fireEvent.change(slider, { target: { value: target.position } });
    // Gate #2's done coincides with chat #3: the active turn alone has no gate.
    expect(screen.getAllByRole('article', { name: /客服回复/ })).toHaveLength(visibleTurns);
    fireEvent.click(screen.getByRole('button', { name: `查看此轮 ${target.turn}` }));
    fireEvent.click(screen.getByRole('button', { name: /置信度闸/ }));
    const gate = screen.getByRole('region', { name: '置信度闸详情' });
    expect(within(gate).getByText(status, { exact: true })).toBeInTheDocument();
    expect(within(gate).getByRole('img', { name: /置信度/ })).toBeInTheDocument();
  });

  it.each([
    ['player', ''], ['player', '?t=0&view=eng'],
    ['app', ''], ['app', '?t=0&view=eng'],
  ])('starts on the first play click from a zero-time deep link (%s, %s)', async (mode, query) => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
    await act(async () => {
      if (mode === 'player') {
        window.history.replaceState(null, '', `#/theater/flywheel${query}`);
        render(<StrictMode><ViewModeProvider><PlayerPage sceneId="flywheel" /></ViewModeProvider></StrictMode>);
      }
      else {
        window.history.replaceState(null, '', `#/theater/flywheel${query}`);
        render(<StrictMode><App /></StrictMode>);
      }
    });
    const slider = screen.getByRole('slider', { name: '播放进度' });
    expect(slider).toHaveValue('0');
    expect(window.location.hash).toBe('#/theater/flywheel?t=0&view=eng');
    const button = screen.getByRole('button', { name: '播放' });
    fireEvent.pointerDown(button);
    fireEvent.mouseDown(button);
    act(() => button.focus());
    fireEvent.pointerUp(button);
    fireEvent.mouseUp(button);
    fireEvent.click(button);
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1008));
    expect(slider).toHaveValue('1008');
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument();
  });
  it('pauses inside a narration hold and resumes on one click through its remaining duration', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
    await act(async () => { mount('flywheel', '?t=0&view=eng'); });
    const slider = screen.getByRole('slider', { name: '播放进度' });
    // The first hold spans 347–3347 ms; later recording events must wait for its end.
    fireEvent.click(screen.getByRole('button', { name: '播放' }));
    act(() => vi.advanceTimersByTime(1008));
    expect(slider).toHaveValue('1008');
    expect(screen.getByText('客户问 L2 是否支持语音控制。现有知识缺少这一项。')).toBeInTheDocument();
    expect(screen.queryByText(/会话 #215/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '暂停' }));
    act(() => vi.advanceTimersByTime(2000));
    expect(slider).toHaveValue('1008');
    fireEvent.click(screen.getByRole('button', { name: '播放' }));
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1008));
    expect(slider).toHaveValue('2016');
    expect(screen.queryByText(/会话 #215/)).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1600));
    expect(slider).toHaveValue('3616');
    expect(screen.getByText(/会话 #215/)).toBeInTheDocument();
  });
  it('plays on one click after dragging into the middle of a narration hold', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
    await act(async () => { mount('flywheel', '?t=0&view=eng'); });
    const slider = screen.getByRole('slider', { name: '播放进度' });
    fireEvent.change(slider, { target: { value: '1800' } });
    expect(slider).toHaveValue('1800');
    expect(window.location.hash).toBe('#/theater/flywheel?t=1.8&view=eng');
    expect(screen.getByText('客户问 L2 是否支持语音控制。现有知识缺少这一项。')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1008));
    expect(slider).toHaveValue('1800');
    fireEvent.click(screen.getByRole('button', { name: '播放' }));
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1008));
    expect(slider).toHaveValue('2808');
    expect(screen.queryByText(/会话 #215/)).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1008));
    expect(slider).toHaveValue('3816');
    expect(screen.getByText(/会话 #215/)).toBeInTheDocument();
  });
  it('fits the remaining viewport from document coordinates even after scrolled gallery navigation', async () => {
    vi.stubGlobal('innerHeight', 900);
    vi.stubGlobal('scrollY', 250);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      top: -100, bottom: 0, left: 0, right: 1440, width: 1440, height: 100, x: 0, y: -100, toJSON: () => ({}),
    });
    window.history.replaceState(null, '', '#/theater/flywheel?t=2');
    render(<ViewModeProvider><header /><main style={{ padding: 24, margin: 24, border: '1px solid' }}>
      <PlayerPage sceneId="flywheel" />
    </main></ViewModeProvider>);
    await screen.findByRole('slider');
    const player = screen.getByRole('heading', { name: '知识飞轮：从兜底到答对' }).closest('section')!;
    expect(player.style.getPropertyValue('--player-height')).toBe('701px');
    expect(player.style.getPropertyValue('--narration-top')).toBe('250px');
  });
  it('collapses split xray to a node-count bar and expands it inside the customer lane', async () => {
    mount('flywheel', '?t=999&view=eng');
    const customer = await screen.findByRole('region', { name: '客户侧' });
    const expand = within(customer).getByRole('button', { name: /透视面板 · \d+ 个节点/ });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    expect(within(customer).queryByLabelText('透视面板')).not.toBeInTheDocument();
    fireEvent.click(expand);
    expect(expand).toHaveAttribute('aria-expanded', 'true');
    expect(within(customer).getByLabelText('透视面板')).toBeInTheDocument();
    fireEvent.click(expand);
    expect(within(customer).queryByLabelText('透视面板')).not.toBeInTheDocument();
  });
  it('defaults to automatic holds, shows hold spans, persists disabling and restores original duration', async () => {
    const page = mount('flywheel', '?t=2&view=eng');
    const slider = await screen.findByRole('slider');
    const toggle = screen.getByRole('checkbox', { name: '自动停留' });
    expect(toggle).toBeChecked();
    expect(slider).toHaveAttribute('max', '26910');
    expect(slider).toHaveValue('2000');
    expect(screen.getByLabelText('解说停留区间').children).toHaveLength(6);
    expect(screen.getByText('客户问 L2 是否支持语音控制。现有知识缺少这一项。')).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(slider).toHaveAttribute('max', '8910');
    expect(slider).toHaveValue('347');
    expect(window.location.hash).toBe('#/theater/flywheel?t=0.347&view=eng');
    expect(localStorage.getItem('theater_auto_hold')).toBe('false');
    page.unmount();
    mount('flywheel');
    await screen.findByRole('slider');
    expect(screen.getByRole('checkbox', { name: '自动停留' })).not.toBeChecked();
    expect(screen.getByRole('slider')).toHaveAttribute('max', '8910');
  });
  it('restores hash changes and chapter seeks on the expanded timeline', async () => {
    mount('flywheel', '?t=2');
    await screen.findByRole('slider');
    act(() => {
      window.history.replaceState(null, '', '#/theater/flywheel?t=5&view=cust');
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(screen.getByRole('slider')).toHaveValue('5000');
    expect(screen.queryByRole('button', { name: /透视面板 ·/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^章节 2：/ }));
    expect(screen.getByRole('slider')).toHaveValue('6435');
    expect(window.location.hash).toMatch(/view=cust$/);
  });
  it('restores deep-link time and customer view, and replaces URL when seeking or switching view', async () => {
    localStorage.setItem('theater_auto_hold', 'false');
    const replace = vi.spyOn(window.history, 'replaceState');
    mount('refund', '?t=2&view=cust');
    const slider = await screen.findByRole('slider', { name: '播放进度' });
    expect(slider).toHaveValue('2000');
    expect(screen.queryByLabelText('透视面板')).not.toBeInTheDocument();
    expect(screen.getByText('我上周买的耳机想退')).toBeInTheDocument();
    expect(screen.getByText('选择订单 205925')).toBeInTheDocument();
    fireEvent.change(slider, { target: { value: '1000' } });
    expect(window.location.hash).toBe('#/theater/refund?t=1&view=cust');
    expect(screen.queryByText('选择订单 205925')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换工程' }));
    await waitFor(() => expect(window.location.hash).toBe('#/theater/refund?t=1&view=eng'));
    expect(screen.getByLabelText('透视面板')).toBeInTheDocument();
    expect(replace).toHaveBeenCalled();
  });
  it('renders customer and ops lanes including retrieval snapshot and approval result', async () => {
    localStorage.setItem('theater_auto_hold', 'false');
    mount('flywheel', '?t=4.54&view=eng');
    const ops = await screen.findByRole('region', { name: '运营侧' });
    expect(within(screen.getByRole('region', { name: '客户侧' })).getByText('L2 台灯可以用小爱同学语音控制吗？')).toBeInTheDocument();
    expect(within(ops).getAllByText('待审').length).toBeGreaterThan(0);
    expect(within(ops).getByText('检索证据置信度低于门槛')).toBeInTheDocument();
    expect(within(ops).getByText('L2 参数')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('slider'), { target: { value: '8910' } });
    expect(within(ops).getByText(/知识块 #481/)).toBeInTheDocument();
    expect(within(ops).getByText(/向量化 1/)).toBeInTheDocument();
    expect(within(ops).getByText('L2 台灯不支持小爱同学等语音助手控制，请用灯身触控按键调节亮度和色温。')).toBeInTheDocument();
  });
  it('skips to the next cue, shows chapter ticks and real gap labels', async () => {
    localStorage.setItem('theater_auto_hold', 'false');
    mount('mcp-timeout', '?t=1&view=eng');
    await screen.findByRole('slider');
    fireEvent.click(screen.getByRole('button', { name: '跳过等待' }));
    expect(screen.getByRole('slider')).toHaveValue('4458');
    expect(screen.getByText('⏩ 实际 15.483 s')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /章节/ }).length).toBeGreaterThanOrEqual(4);
    fireEvent.change(screen.getByRole('slider'), { target: { value: '999999' } });
    expect(screen.getByRole('button', { name: '跳过等待' })).toBeDisabled();
  });
  it('plays with the clock, changes speed, pauses and stops at the end', async () => {
    localStorage.setItem('theater_auto_hold', 'false');
    mount('boundaries');
    await screen.findByRole('slider');
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole('button', { name: '播放' }));
    act(() => vi.advanceTimersByTime(1000));
    expect(Number((screen.getByRole('slider') as HTMLInputElement).value)).toBeGreaterThan(900);
    fireEvent.change(screen.getByRole('combobox', { name: '播放速度' }), { target: { value: '4' } });
    act(() => vi.advanceTimersByTime(1000));
    expect(Number((screen.getByRole('slider') as HTMLInputElement).value)).toBeGreaterThan(4900);
    fireEvent.click(screen.getByRole('button', { name: '暂停' }));
    const paused = (screen.getByRole('slider') as HTMLInputElement).value;
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('slider')).toHaveValue(paused);
    fireEvent.click(screen.getByRole('button', { name: '播放' }));
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument();
    expect(screen.getByRole('slider')).toHaveValue('6495');
  });
  it('keeps recorded refunds and all turns, and shows commit provenance', async () => {
    mount('refund', '?t=999');
    const commit = await screen.findByRole('link', { name: /^31e2276/ });
    expect(commit).toHaveAttribute('href', 'https://github.com/codelformat/Aftersales-agent/commit/31e2276507d0802bfd972af05793929cc30567b7');
    expect(screen.getByText(/deepseek-v4-flash/)).toBeInTheDocument();
    expect(screen.getByText(/R202610092247/)).toBeInTheDocument();
    expect(screen.getAllByRole('article', { name: /客服回复/ })).toHaveLength(2);
    expect(screen.getByRole('button', { name: '提交退款单' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '新对话' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));
    expect(screen.getByText(/The refund API returns/)).toBeInTheDocument();
  });
  it('uses the strategy snapshot, without requesting a seventh recording', async () => {
    mount('strategies');
    expect(await screen.findByRole('table', { name: '四策略指标对比' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'C01 bm25 Top-5' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '438f85a' })).toHaveAttribute('href', 'https://github.com/codelformat/Aftersales-agent/commit/438f85ac58176247b3358de226e9922b189e2941');
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).not.toContain('/replays/strategies.jsonl');
  });
  it('handles unknown scenes and recording failures', async () => {
    const page = mount('missing');
    expect(screen.getByRole('alert')).toHaveTextContent('场景不存在');
    page.unmount();
    vi.mocked(fetch).mockResolvedValue(new Response('', { status: 404 }));
    mount('refund');
    expect(await screen.findByRole('alert')).toHaveTextContent('录制加载失败');
  });
});
