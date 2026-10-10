import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { useViewMode, ViewModeProvider } from '../app/ViewModeContext';
import DeskPage from './DeskPage';
import { done, FakeDataSource, sequence, session, token } from './test-utils';
import { turn } from '../xray/test-fixtures';
import type { ChatEvent } from '../protocol/events';
beforeEach(() => { localStorage.clear(); localStorage.setItem('view_mode', 'eng'); });
function mount(source: FakeDataSource) { render(<ViewModeProvider><DeskPage dataSource={source} /></ViewModeProvider>); }
async function send(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByRole('textbox', { name: '输入您的问题' }), text);
  await user.click(screen.getByRole('button', { name: '发送' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '输入您的问题' })).toBeEnabled());
  return user;
}
it('connects citation hover to retrieval rows and reply clicks to the selected trace', async () => {
  const source = new FakeDataSource();
  const traces: ChatEvent[] = turn.trace.map(data => ({ event: 'trace', data }));
  source.chat.mockReturnValueOnce(sequence([session, ...traces, token('退款政策 [1]'),
    { event: 'citations', data: { items: turn.citations!, refused: false } }, done]));
  mount(source);
  await send('退款问题');
  const panel = within(screen.getByRole('complementary', { name: '透视面板' }));
  expect(panel.getByRole('button', { name: /检索.*retrieve_multi/ })).toBeInTheDocument();
  const marker = screen.getByRole('button', { name: '查看引用 1 的原文' });
  fireEvent.pointerMove(marker, { pointerType: 'mouse' });
  await waitFor(() => expect(panel.getByRole('listitem', { name: /退款政策\/期限/ })).toHaveAttribute('data-highlighted', 'true'));
  fireEvent.pointerLeave(marker); fireEvent.pointerMove(document.body, { clientX: 1000, clientY: 1000 });
  await waitFor(() => expect(panel.queryByRole('listitem', { name: /退款政策\/期限/ })).not.toBeInTheDocument());
  const user = await send('第二个问题');
  expect(panel.getByText('该轮没有调试数据')).toBeInTheDocument();
  fireEvent.pointerMove(marker, { pointerType: 'mouse' });
  await waitFor(() => expect(panel.getByRole('listitem', { name: /退款政策\/期限/ })).toHaveAttribute('data-highlighted', 'true'));
  fireEvent.pointerLeave(marker); fireEvent.pointerMove(document.body, { clientX: 1000, clientY: 1000 });
  await waitFor(() => expect(panel.getByText('该轮没有调试数据')).toBeInTheDocument());
  await user.click(screen.getByRole('article', { name: '客服回复 1' }));
  expect(panel.getByRole('button', { name: /检索.*retrieve_multi/ })).toBeInTheDocument();
  await user.click(screen.getByRole('article', { name: '客服回复 2' }));
  expect(panel.getByText('该轮没有调试数据')).toBeInTheDocument();
});
it('hides the entire panel in customer view', async () => {
  localStorage.setItem('view_mode', 'customer'); mount(new FakeDataSource()); await send('你好');
  expect(screen.queryByRole('complementary', { name: '透视面板' })).not.toBeInTheDocument();
});

function ViewControls() {
  const { setViewMode } = useViewMode();
  return <><button onClick={() => setViewMode('customer')}>客户视角</button><button onClick={() => setViewMode('eng')}>工程视角</button></>;
}
it('keeps the panel collapse control and layout consistent after changing view', async () => {
  const { container } = render(<ViewModeProvider><ViewControls /><DeskPage dataSource={new FakeDataSource()} /></ViewModeProvider>);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: '收起透视面板' }));
  await user.click(screen.getByRole('button', { name: '客户视角' }));
  await user.click(screen.getByRole('button', { name: '工程视角' }));
  expect(screen.getByRole('button', { name: '展开透视面板' })).toHaveAttribute('aria-expanded', 'false');
  expect(container.querySelector('[data-view]')).toHaveAttribute('data-xray-collapsed', 'true');
});
