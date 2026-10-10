import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ViewModeProvider } from '../app/ViewModeContext';
import { ApiError } from '../data/DataSource';
import DeskPage from './DeskPage';
import { deferred, done, eventStream, FakeDataSource, interrupted, order, sequence, session, token } from './test-utils';
import type { Action, ChatEvent } from '../protocol/events';

beforeEach(() => { localStorage.clear(); localStorage.setItem('view_mode', 'customer'); });
function mount(source: FakeDataSource, props: Partial<React.ComponentProps<typeof DeskPage>> = {}) {
  return render(<ViewModeProvider><DeskPage dataSource={source} {...props} /></ViewModeProvider>);
}
async function send(text = '我要退款') {
  const user = userEvent.setup();
  await user.type(screen.getByRole('textbox', { name: '输入您的问题' }), text);
  await user.click(screen.getByRole('button', { name: '发送' }));
  return user;
}
function withActions(source: FakeDataSource, options: Action[]) {
  source.chat.mockReturnValue(sequence([session, token('请办理'), { event: 'actions', data: { options } }, done]));
}

describe('service desk interactions', () => {
  it('shows streaming tokens and understood text, and disables input, new chat and sessions while sending', async () => {
    const source = new FakeDataSource();
    const stream = eventStream();
    source.chat.mockReturnValueOnce(stream);
    source.conversations.mockResolvedValue([{ session_id: '9', preview: '旧会话', summarized: true, created_at: '', updated_at: '2026-10-09T10:00:00' }]);
    mount(source);
    await screen.findByRole('button', { name: /旧会话/ });
    await send();
    expect(screen.getByRole('textbox', { name: '输入您的问题' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '新对话' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /旧会话/ })).toBeDisabled();
    await act(async () => {
      stream.push(session);
      stream.push({ event: 'understood', data: { resolved_input: '空气净化器退款', intent: '退款退货' } });
      stream.push(token('正在处理'));
    });
    expect(screen.getByText(/已理解为：空气净化器退款/)).toBeInTheDocument();
    expect(screen.getByText('正在处理')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '有帮助' })).not.toBeInTheDocument();
    await act(async () => { stream.push(token('退款')); stream.push(done); stream.close(); });
    expect(await screen.findByText('正在处理退款')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('textbox', { name: '输入您的问题' })).toBeEnabled());
  });

  it('hides understood when it equals the original message and displays tool lifecycle status', async () => {
    const source = new FakeDataSource();
    source.chat.mockReturnValue(sequence([session,
      { event: 'understood', data: { resolved_input: '我要退款', intent: null } },
      { event: 'tool_start', data: { tools: [{ id: 't1', name: 'query_order', args: { order_id: '1001' } }] } },
      { event: 'tool_end', data: { tools: [{ id: 't1', name: 'query_order', ok: true }] } }, token('已查询'), done]));
    mount(source); await send();
    expect(await screen.findByText('已查询')).toBeInTheDocument();
    expect(screen.queryByText(/已理解为/)).not.toBeInTheDocument();
    expect(screen.getByText(/查订单.*已完成/)).toBeInTheDocument();
  });

  it('selects an order, resumes in the same turn and shows the selected order in the left rail', async () => {
    const source = new FakeDataSource();
    source.chat.mockReturnValue(sequence([session, token('需要订单。'), { event: 'order_picker', data: { orders: [order] } }, interrupted]));
    mount(source); const user = await send();
    const picker = await screen.findByRole('button', { name: /选择订单 1001/ });
    await user.click(picker);
    expect(await screen.findByText('需要订单。已处理')).toBeInTheDocument();
    expect(screen.getAllByRole('article', { name: /客服回复/ })).toHaveLength(1);
    expect(source.resume).toHaveBeenCalledWith({ user_id: expect.stringMatching(/^web-/), session_id: '42', order_id: '1001' });
    expect(within(screen.getByRole('complementary', { name: '会话侧栏' })).getByText('空气净化器')).toBeInTheDocument();
    expect(screen.getByText('选择订单 1001')).toBeInTheDocument();
  });

  it('submits a refund with reason and trimmed note, shows success and blocks duplicate submissions', async () => {
    const source = new FakeDataSource(); withActions(source, [{ type: 'refund', order_id: '1001' }]);
    mount(source); const user = await send();
    await user.click(await screen.findByRole('button', { name: '提交退款单' }));
    expect(screen.getByRole('button', { name: '提交' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('退款原因'), '质量问题');
    await user.type(screen.getByLabelText('备注（可选）'), '  外壳破损  ');
    expect(screen.getByText('剩余 192 字')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText(/已为您提交退款申请 RF-9.*待审核/)).toBeInTheDocument();
    expect(source.submitRefund).toHaveBeenCalledWith({ session_id: '42', user_id: expect.any(String), order_id: '1001', reason: '质量问题', note: '外壳破损' });
    expect(screen.getByRole('button', { name: '已提交' })).toBeDisabled();
  });

  it('keeps refund form values after failure and permits a user retry', async () => {
    const source = new FakeDataSource(); withActions(source, [{ type: 'refund', order_id: '1001' }]);
    source.submitRefund.mockRejectedValueOnce(new Error('退款申请提交失败'));
    mount(source); const user = await send();
    await user.click(await screen.findByRole('button', { name: '提交退款单' }));
    await user.selectOptions(screen.getByLabelText('退款原因'), '其他');
    await user.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('退款申请提交失败');
    expect(screen.getByLabelText('退款原因')).toHaveValue('其他');
    await user.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText(/RF-9/)).toBeInTheDocument();
  });

  it.each([true, false])('resumes ticket preview with ticket_confirm=%s', async confirmed => {
    const source = new FakeDataSource();
    source.chat.mockReturnValue(sequence([session, { event: 'ticket_preview', data: { call_id: 'c1', ticket_type: '投诉', description: '快递丢失' } }, interrupted]));
    source.resume.mockReturnValue(sequence([token(confirmed ? '工单已创建' : '已取消创建'), done]));
    mount(source); const user = await send('建工单');
    expect(await screen.findByText('问题描述：快递丢失')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: confirmed ? '确认提交' : '取消' }));
    expect(await screen.findByText(confirmed ? '工单已创建' : '已取消创建')).toBeInTheDocument();
    expect(source.resume).toHaveBeenCalledWith({ session_id: '42', user_id: expect.any(String), ticket_confirm: confirmed });
    expect(screen.getAllByRole('article', { name: /客服回复/ })).toHaveLength(1);
  });

  it('explains an expired ticket confirmation', async () => {
    const source = new FakeDataSource();
    source.chat.mockReturnValue(sequence([session, { event: 'ticket_preview', data: { call_id: 'c1', ticket_type: '售后', description: '破损' } }, interrupted]));
    source.resume.mockImplementation(() => { throw new ApiError(409, 'no_pending_ticket', 'raw'); });
    mount(source); const user = await send();
    await user.click(await screen.findByRole('button', { name: '确认提交' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('该工单已失效，请重新发起');
  });

  it('confirms handoff locally and shows the human welcome without creating a ticket', async () => {
    const source = new FakeDataSource(); withActions(source, [{ type: 'handoff' }]);
    mount(source); const user = await send('转人工');
    await user.click(await screen.findByRole('button', { name: '转人工' }));
    await user.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByText('已转接人工客服')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '转人工' }));
    await user.click(screen.getByRole('button', { name: '确认' }));
    expect(await screen.findByText('已转接人工客服')).toBeInTheDocument();
    expect(screen.getByText('您好，我是客服小猫，请问有什么可以帮您的')).toBeInTheDocument();
    expect(source.submitTicket).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '转人工' })).toBeDisabled();
  });

  it('creates a button ticket only after confirmation with an editable description', async () => {
    const source = new FakeDataSource(); withActions(source, [{ type: 'ticket', ticket_type: '投诉', description: '快递丢失' }]);
    mount(source); const user = await send('投诉');
    await user.click(await screen.findByRole('button', { name: '建工单' }));
    expect(screen.getByLabelText('描述')).toHaveAttribute('maxlength', '500');
    await user.clear(screen.getByLabelText('描述')); await user.type(screen.getByLabelText('描述'), '包裹一直没收到');
    expect(source.submitTicket).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认' }));
    expect(await screen.findByText('工单已创建：TK-9')).toBeInTheDocument();
    expect(source.submitTicket).toHaveBeenCalledWith({ session_id: '42', user_id: expect.any(String), ticket_type: '投诉', description: '包裹一直没收到' });
  });

  it.each(['up', 'down'] as const)('sends %s feedback only after done carries a message ID and persists success', async rating => {
    const source = new FakeDataSource(); mount(source); const user = await send();
    const button = await screen.findByRole('button', { name: rating === 'up' ? '有帮助' : '没有帮助' });
    await user.click(button);
    expect(source.feedback).toHaveBeenCalledWith({ conversation_id: 42, message_id: 81, user_id: expect.any(String), rating });
    expect(button).toHaveAttribute('aria-pressed', 'true');
    expect(button).toBeDisabled();
    expect(JSON.parse(localStorage.getItem('aftersales_feedback')!)[0]).toMatchObject({ session_id: '42', answer_index: 0, rating });
  });

  it('allows feedback retry after a failed submission', async () => {
    const source = new FakeDataSource(); source.feedback.mockRejectedValueOnce(new Error('offline'));
    mount(source); const user = await send();
    const button = await screen.findByRole('button', { name: '没有帮助' }); await user.click(button);
    expect(await screen.findByText('反馈失败')).toBeInTheDocument(); expect(button).toBeEnabled();
    await user.click(button); expect(await screen.findByText('已反馈，我们会改进')).toBeInTheDocument();
    expect(source.feedback).toHaveBeenCalledTimes(2);
  });

  it.each([
    { event: 'done', data: { finish_reason: 'stop' } },
    { event: 'done', data: { finish_reason: 'interrupted', message_id: 81 } },
    { event: 'error', data: { code: 'upstream_error', message: '服务暂时不可用' } },
  ] satisfies ChatEvent[])('does not offer feedback without an eligible done event: %j', async event => {
    const source = new FakeDataSource(); source.chat.mockReturnValue(sequence([session, token('答复'), event]));
    mount(source); await send(); await screen.findByText('答复');
    expect(screen.queryByRole('button', { name: '有帮助' })).not.toBeInTheDocument();
  });

  it('displays an error event and releases the composer', async () => {
    const source = new FakeDataSource(); source.chat.mockReturnValue(sequence([session, { event: 'error', data: { code: 'upstream_error', message: '上游暂时不可用，请稍后重试' } }]));
    mount(source); await send();
    expect(await screen.findByRole('alert')).toHaveTextContent('上游暂时不可用，请稍后重试');
    expect(screen.getByRole('textbox', { name: '输入您的问题' })).toBeEnabled();
  });

  it('loads a sidebar conversation without replaying trace or showing feedback', async () => {
    const source = new FakeDataSource();
    source.conversations.mockResolvedValue([{ session_id: '10', preview: '退款历史', summarized: true, created_at: '', updated_at: '2026-10-09T10:00:00' }]);
    source.messages.mockResolvedValue([{ id: 1, role: 'user', content: '历史问题', created_at: '' }, { id: 2, role: 'assistant', content: '历史答复 [1]', created_at: '' }]);
    const selected = vi.fn(); mount(source, { onSelectedTurnChange: selected });
    const user = userEvent.setup(); await user.click(await screen.findByRole('button', { name: /退款历史/ }));
    expect(await screen.findByText('历史答复 [1]')).toBeInTheDocument();
    expect(screen.getByText('历史问题')).toBeInTheDocument();
    expect(screen.getByText('已摘要')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '有帮助' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /查看此轮/ }));
    expect(selected).toHaveBeenCalledWith(expect.any(String));
    await user.click(screen.getByRole('button', { name: '新对话' }));
    expect(screen.queryByText('历史问题')).not.toBeInTheDocument();
    await send('新问题'); expect(source.chat.mock.calls[0][0].session_id).toBeUndefined();
  });

  it('uses a Radix evidence tooltip, notifies citation hover, and opens original chunks with previous/next navigation', async () => {
    const source = new FakeDataSource();
    source.chat.mockReturnValue(sequence([session, token('退款需三个工作日 [1]。未知 [9]。'), { event: 'citations', data: { items: [{ n: 1, chunk_id: 7, section_path: '退款政策', question: '多久退款？', answer: '证据：三个工作日。' }], refused: false } }, done]));
    const hover = vi.fn(); mount(source, { onCitationHover: hover }); const user = await send();
    const marker = await screen.findByRole('button', { name: '查看引用 1 的原文' });
    fireEvent.pointerMove(marker, { pointerType: 'mouse' });
    expect(await screen.findByRole('tooltip')).toHaveTextContent('证据：三个工作日。');
    expect(hover).toHaveBeenCalledWith(expect.any(String), 7);
    fireEvent.pointerLeave(marker);
    fireEvent.pointerMove(document.body, { clientX: 1000, clientY: 1000 });
    await waitFor(() => expect(hover).toHaveBeenLastCalledWith(expect.any(String), null));
    await user.click(marker);
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/原文：三个工作日/)).toBeInTheDocument();
    expect(source.knowledgeChunk).toHaveBeenCalledWith(7);
    await user.click(within(dialog).getByRole('button', { name: '下一段' })); expect(source.knowledgeChunk).toHaveBeenLastCalledWith(8);
    await user.click(within(dialog).getByRole('button', { name: '上一段' })); expect(source.knowledgeChunk).toHaveBeenLastCalledWith(6);
    await user.keyboard('{Escape}'); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(marker).toHaveFocus();
  });

  it('shows chunk load failure with inline evidence and permits retry', async () => {
    const source = new FakeDataSource(); source.knowledgeChunk.mockRejectedValueOnce(new Error('offline'));
    source.chat.mockReturnValue(sequence([session, token('政策 [1]'), { event: 'citations', data: { items: [{ n: 1, chunk_id: 7, section_path: '退款政策', question: '退款？', answer: '证据仍可读' }], refused: false } }, done]));
    mount(source); const user = await send(); await user.click(await screen.findByRole('button', { name: '查看引用 1 的原文' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('原文加载失败');
    expect(screen.getByRole('dialog')).toHaveTextContent('证据仍可读');
    await user.click(screen.getByRole('button', { name: '重试加载原文' }));
    expect(await screen.findByText(/原文：三个工作日/)).toBeInTheDocument();
  });

  it('supports Enter, Shift+Enter, IME composition, empty guards and the 2000 character limit', async () => {
    const source = new FakeDataSource(); mount(source); const user = userEvent.setup();
    const input = screen.getByRole('textbox', { name: '输入您的问题' });
    expect(input).toHaveAttribute('maxlength', '2000'); expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    await user.type(input, '问题'); await user.keyboard('{Shift>}{Enter}{/Shift}'); expect(source.chat).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 }); expect(source.chat).not.toHaveBeenCalled();
    await user.keyboard('{Enter}'); await screen.findByText('您好'); expect(source.chat).toHaveBeenCalledTimes(1);
  });

  it('shows the perspective placeholder only in engineering view', async () => {
    const source = new FakeDataSource(); const mounted = mount(source);
    expect(screen.queryByRole('complementary', { name: '透视面板' })).not.toBeInTheDocument(); mounted.unmount();
    localStorage.setItem('view_mode', 'eng'); mount(source);
    expect(screen.getByRole('complementary', { name: '透视面板' })).toHaveTextContent('透视面板将在下一任务接入');
  });

  it('locks the refund form and prevents closing while a submission is pending', async () => {
    const source = new FakeDataSource(); withActions(source, [{ type: 'refund', order_id: '1001' }]);
    const pending = deferred<Awaited<ReturnType<typeof source.submitRefund>>>(); source.submitRefund.mockReturnValueOnce(pending.promise);
    mount(source); const user = await send(); await user.click(await screen.findByRole('button', { name: '提交退款单' }));
    await user.selectOptions(screen.getByLabelText('退款原因'), '其他'); await user.click(screen.getByRole('button', { name: '提交' }));
    expect(screen.getByLabelText('退款原因')).toBeDisabled(); expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
    await user.keyboard('{Escape}'); expect(screen.getByRole('dialog')).toBeInTheDocument();
    await act(async () => { pending.resolve({ refund_no: 'RF-9', status: '待审核' }); });
    expect(await screen.findByText(/RF-9/)).toBeInTheDocument();
  });

  it('keeps feedback attached to the original session after an expired conversation starts anew', async () => {
    const source = new FakeDataSource();
    source.chat.mockReturnValueOnce(sequence([session, token('您好'), { event: 'actions', data: { options: [{ type: 'refund', order_id: '1001' }] } }, done]));
    mount(source); const user = await send('第一轮');
    await screen.findByText('您好');
    source.chat.mockImplementationOnce(() => { throw new ApiError(404, 'conversation_not_found', 'expired'); });
    await send('第二轮'); await screen.findByRole('alert');
    source.chat.mockReturnValueOnce(sequence([{ event: 'session', data: { session_id: '43' } }, token('新的答复'),
      { event: 'done', data: { finish_reason: 'stop', message_id: 82 } }]));
    await send('重新发送'); await screen.findByText('新的答复');
    expect(screen.queryByRole('region', { name: '当前订单' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '提交退款单' })).toBeDisabled();
    await user.click(within(screen.getByRole('article', { name: '客服回复 1' })).getByRole('button', { name: '有帮助' }));
    expect(source.feedback).toHaveBeenCalledWith({ conversation_id: 42, message_id: 81, user_id: expect.any(String), rating: 'up' });
  });

  it('notifies a controlled turn selection when the reply body is clicked', async () => {
    const source = new FakeDataSource(); const selected = vi.fn();
    mount(source, { selectedTurnId: 'external', onSelectedTurnChange: selected });
    const user = await send(); const reply = await screen.findByText('您好');
    selected.mockClear(); await user.click(reply);
    expect(selected).toHaveBeenCalledWith(expect.stringMatching(/^[a-f0-9-]{36}$/));
  });

  it('exposes sidebar expansion and restores the toggle focus on Escape and close', async () => {
    const source = new FakeDataSource(); mount(source); const user = userEvent.setup();
    const toggle = screen.getByRole('button', { name: '展开会话侧栏' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle); expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('button', { name: '收起会话侧栏' })); expect(toggle).toHaveFocus();
    await user.click(toggle); await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'false'); expect(toggle).toHaveFocus();
  });
});
