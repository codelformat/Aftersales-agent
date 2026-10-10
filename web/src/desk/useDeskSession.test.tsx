import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ViewModeProvider } from '../app/ViewModeContext';
import { ApiError } from '../data/DataSource';
import { useDeskSession } from './useDeskSession';
import { deferred, done, eventStream, FakeDataSource, interrupted, order, sequence, session, token } from './test-utils';

const wrapper = ({ children }: { children: ReactNode }) => <ViewModeProvider>{children}</ViewModeProvider>;
beforeEach(() => localStorage.clear());

describe('useDeskSession', () => {
  it('streams before done, locks concurrent sends, and preserves the server session', async () => {
    const source = new FakeDataSource();
    const stream = eventStream();
    source.chat.mockReturnValueOnce(stream);
    const { result } = renderHook(() => useDeskSession(source), { wrapper });
    let sending!: Promise<void>;
    act(() => { sending = result.current.send('  我要退款  '); });
    expect(result.current.busy).toBe(true);
    await act(async () => { stream.push(session); stream.push(token('正在')); });
    expect(result.current.state.turns[0].replyText).toBe('正在');
    await act(async () => { await result.current.send('重复'); });
    expect(source.chat).toHaveBeenCalledTimes(1);
    await act(async () => { stream.push(token('处理')); stream.push(done); stream.close(); await sending; });
    expect(result.current.busy).toBe(false);
    expect(result.current.state.turns[0]).toMatchObject({ userText: '我要退款', replyText: '正在处理', messageId: 81 });
    await act(async () => { await result.current.send('继续'); });
    expect(source.chat).toHaveBeenLastCalledWith(expect.objectContaining({ session_id: '42', debug: true }));
  });

  it.each(['eng', 'customer'])('sends chat and resume with the correct debug flag in %s view', async mode => {
    localStorage.setItem('view_mode', mode);
    const source = new FakeDataSource();
    source.chat.mockReturnValue(sequence([session, { event: 'order_picker', data: { orders: [order] } }, interrupted]));
    const { result } = renderHook(() => useDeskSession(source), { wrapper });
    await act(async () => { await result.current.send('退款'); });
    await act(async () => { await result.current.resumeOrder(result.current.state.turns[0].id, order); });
    const debug = mode === 'eng' ? true : undefined;
    expect(source.chat.mock.calls[0][0].debug).toBe(debug);
    expect(source.resume.mock.calls[0][0]).toEqual({ session_id: '42', user_id: result.current.userId, order_id: '1001', ...(debug ? { debug } : {}) });
    expect(result.current.state.turns).toHaveLength(1);
    expect(result.current.state.turns[0].replyText).toBe('已处理');
    expect(result.current.selectedOrder).toEqual(order);
  });

  it('keeps a valid ch09 user ID and replaces an invalid ID with eight random bytes', async () => {
    localStorage.setItem('aftersales_user_id', 'web-0123456789abcdef');
    const source = new FakeDataSource();
    const first = renderHook(() => useDeskSession(source), { wrapper });
    expect(first.result.current.userId).toBe('web-0123456789abcdef');
    first.unmount();
    localStorage.setItem('aftersales_user_id', 'invalid');
    const second = renderHook(() => useDeskSession(source), { wrapper });
    expect(second.result.current.userId).toMatch(/^web-[a-f0-9]{16}$/);
    expect(localStorage.getItem('aftersales_user_id')).toBe(second.result.current.userId);
    await waitFor(() => expect(source.conversations).toHaveBeenLastCalledWith(second.result.current.userId));
  });

  it('reuses the in-memory ID when storage throws', async () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    const source = new FakeDataSource();
    try {
      const { result } = renderHook(() => useDeskSession(source), { wrapper });
      expect(result.current.userId).toMatch(/^web-[a-f0-9]{16}$/);
      await act(async () => { await result.current.send('第一条'); await result.current.send('第二条'); });
      expect(source.chat.mock.calls[0][0].user_id).toBe(source.chat.mock.calls[1][0].user_id);
    } finally { get.mockRestore(); set.mockRestore(); }
  });

  it('loads history as turns without trace, citations, or feedback eligibility', async () => {
    const source = new FakeDataSource();
    source.messages.mockResolvedValue([
      { id: 1, role: 'user', content: '我的订单', created_at: '2026-10-09' },
      { id: 2, role: 'assistant', content: '已发货', created_at: '2026-10-09' },
      { id: 3, role: 'assistant', content: '工单已创建', created_at: '2026-10-09' },
    ]);
    const { result } = renderHook(() => useDeskSession(source), { wrapper });
    await act(async () => { await result.current.loadConversation('10'); });
    expect(source.messages).toHaveBeenCalledWith(10, result.current.userId);
    expect(result.current.state.sessionId).toBe('10');
    expect(result.current.state.turns.map(turn => turn.replyText)).toEqual(['已发货', '工单已创建']);
    expect(result.current.state.turns.every(turn => turn.trace.length === 0 && turn.messageId === undefined)).toBe(true);
  });

  it('ignores stale history after another selection or a new conversation', async () => {
    const source = new FakeDataSource();
    const slow = deferred<Awaited<ReturnType<typeof source.messages>>>();
    source.messages.mockReturnValueOnce(slow.promise);
    const { result } = renderHook(() => useDeskSession(source), { wrapper });
    let loading!: Promise<void>;
    act(() => { loading = result.current.loadConversation('10'); });
    await act(async () => { await result.current.loadConversation('11'); });
    await act(async () => { slow.resolve([{ id: 1, role: 'assistant', content: '旧回复', created_at: '' }]); await loading; });
    expect(result.current.state.sessionId).toBe('11');
    const next = deferred<Awaited<ReturnType<typeof source.messages>>>();
    source.messages.mockReturnValueOnce(next.promise);
    act(() => { loading = result.current.loadConversation('12'); });
    act(() => result.current.newSession());
    await act(async () => { next.resolve([]); await loading; });
    expect(result.current.state.sessionId).toBeUndefined();
  });

  it.each([
    [409, 'no_pending_selection', '订单选择已失效'],
    [422, 'invalid_order', '订单选择已失效'],
  ])('explains stale order resume HTTP %s', async (status, code, message) => {
    const source = new FakeDataSource();
    source.chat.mockReturnValue(sequence([session, { event: 'order_picker', data: { orders: [order] } }, interrupted]));
    source.resume.mockImplementation(() => { throw new ApiError(status, code, 'raw'); });
    const { result } = renderHook(() => useDeskSession(source), { wrapper });
    await act(async () => { await result.current.send('退款'); });
    await act(async () => { await result.current.resumeOrder(result.current.state.turns[0].id, order); });
    expect(result.current.state.turns[0].error?.message).toContain(message);
    expect(result.current.busy).toBe(false);
  });

  it('resets an expired session before the next send', async () => {
    const source = new FakeDataSource();
    const { result } = renderHook(() => useDeskSession(source), { wrapper });
    await act(async () => { await result.current.send('你好'); });
    source.chat.mockImplementationOnce(() => { throw new ApiError(404, 'conversation_not_found', 'missing'); });
    await act(async () => { await result.current.send('继续'); });
    expect(result.current.state.sessionId).toBeUndefined();
    expect(result.current.state.turns.at(-1)?.error?.message).toContain('会话已失效');
    await act(async () => { await result.current.send('重新发送'); });
    expect(source.chat.mock.calls.at(-1)?.[0].session_id).toBeUndefined();
  });

  it('reports truncated streams and releases sending controls', async () => {
    const source = new FakeDataSource();
    source.chat.mockReturnValueOnce(sequence([session, token('部分答复')]));
    const { result } = renderHook(() => useDeskSession(source), { wrapper });
    await act(async () => { await result.current.send('你好'); });
    expect(result.current.state.turns[0].replyText).toBe('部分答复');
    expect(result.current.state.turns[0].error?.message).toContain('连接已中断');
    expect(result.current.busy).toBe(false);
  });
});
