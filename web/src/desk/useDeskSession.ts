import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { useViewMode } from '../app/ViewModeContext';
import { ApiError } from '../data/DataSource';
import type { ConversationSummary, DataSource, RefundRequest, StoredMessage, TicketRequest } from '../data/DataSource';
import type { Action, ChatEvent, Order } from '../protocol/events';
import { reduce } from '../state/conversation';
import type { DeskAction, DeskState, Turn } from '../state/conversation';

type SessionAction = DeskAction | { type: 'reset' } | { type: 'expire' };
function sessionReducer(state: DeskState, action: SessionAction): DeskState {
  if (action.type === 'reset') return { turns: [] };
  if (action.type === 'expire') return { ...state, sessionId: undefined };
  return reduce(state, action);
}

function userIdentity() {
  try {
    const stored = localStorage.getItem('aftersales_user_id');
    if (stored && /^web-[a-f0-9]{16}$/.test(stored)) return stored;
  } catch { /* Keep the identity in memory if storage is unavailable. */ }
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  const id = `web-${Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')}`;
  try { localStorage.setItem('aftersales_user_id', id); } catch { /* Memory remains available. */ }
  return id;
}

function historyTurns(messages: StoredMessage[]): Turn[] {
  const turns: Turn[] = [];
  for (const message of messages) {
    if (message.role === 'assistant' && turns.at(-1)?.replyText === '') {
      turns[turns.length - 1] = { ...turns[turns.length - 1], replyText: message.content };
    } else {
      turns.push({ id: `history-${message.id}`, userText: message.role === 'user' ? message.content : '',
        replyText: message.role === 'assistant' ? message.content : '', status: 'done', tools: [], trace: [] });
    }
  }
  return turns;
}

export function actionKey(action: Action) { return action.type === 'refund' ? `refund:${action.order_id}` : action.type; }
export type OrderSummary = Pick<Order, 'order_id'> & Partial<Omit<Order, 'order_id'>>;
export interface DeskNotice { kind: 'selection' | 'success' | 'handoff'; text: string }

export function useDeskSession(source: DataSource) {
  const { viewMode } = useViewMode();
  const [userId] = useState(userIdentity);
  const [state, rawDispatch] = useReducer(sessionReducer, { turns: [] });
  const stateRef = useRef(state);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [listError, setListError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [chosenOrder, setChosenOrder] = useState<Order>();
  const chosenOrderSession = useRef<string | undefined>(undefined);
  const [notices, setNotices] = useState<Record<string, DeskNotice[]>>({});
  const [completed, setCompleted] = useState<Record<string, string[]>>({});
  const completedRef = useRef<Record<string, string[]>>({});
  const [turnSessions, setTurnSessions] = useState<Record<string, string>>({});
  const turnSessionsRef = useRef<Record<string, string>>({});
  const historyVersion = useRef(0);
  const listVersion = useRef(0);
  const lifetime = useRef(0);
  const mounted = useRef(true);

  // Keep imperative guards current even between two calls in the same render batch.
  function dispatch(action: SessionAction) {
    stateRef.current = sessionReducer(stateRef.current, action);
    rawDispatch(action);
  }

  const refreshConversations = useCallback(async () => {
    const version = ++listVersion.current;
    try {
      const items = await source.conversations(userId);
      if (!mounted.current || version !== listVersion.current) return;
      setConversations(items); setListError('');
    } catch {
      if (mounted.current && version === listVersion.current) setListError('会话列表加载失败');
    }
  }, [source, userId]);

  useEffect(() => {
    mounted.current = true;
    void refreshConversations();
    return () => {
      mounted.current = false; lifetime.current++; historyVersion.current++; listVersion.current++;
    };
  }, [refreshConversations]);

  function lock() {
    if (busyRef.current || !mounted.current) return false;
    busyRef.current = true; setBusy(true);
    historyVersion.current++; setLoadingHistory(false); setHistoryError('');
    return true;
  }
  function unlock() { busyRef.current = false; if (mounted.current) setBusy(false); }
  function notice(turnId: string, item: DeskNotice) {
    setNotices(previous => ({ ...previous, [turnId]: [...(previous[turnId] ?? []), item] }));
  }
  function attachSession(turnId: string, sessionId: string) {
    turnSessionsRef.current = { ...turnSessionsRef.current, [turnId]: sessionId };
    setTurnSessions(turnSessionsRef.current);
  }

  function errorMessage(error: unknown, resume?: 'order' | 'ticket') {
    if (error instanceof ApiError) {
      if (error.status === 404 && error.code === 'conversation_not_found') {
        dispatch({ type: 'expire' });
        return '会话已失效，已为您开始新对话，请重新发送。';
      }
      if (resume === 'ticket' && error.status === 409) return '该工单已失效，请重新发起';
      if (resume === 'order' && (error.status === 409 || error.status === 422)) return '订单选择已失效，请重新提问';
      if (error.status === 409) return '当前正在回复中，请稍后再试';
    }
    if (error instanceof TypeError) return '网络连接失败，请检查网络后重试';
    return error instanceof Error ? error.message : '服务暂时不可用，请稍后重试';
  }

  async function consume(turnId: string, events: () => AsyncIterable<ChatEvent>, resume?: 'order' | 'ticket') {
    const version = lifetime.current;
    try {
      let terminal = false;
      for await (const event of events()) {
        if (!mounted.current || version !== lifetime.current) return;
        dispatch({ type: 'event', turnId, event });
        if (event.event === 'session') { attachSession(turnId, event.data.session_id); void refreshConversations(); }
        if (event.event === 'done' || event.event === 'error') { terminal = true; break; }
      }
      if (!terminal) throw new Error('连接已中断，请稍后重试');
    } catch (error) {
      if (mounted.current && version === lifetime.current) {
        dispatch({ type: 'event', turnId, event: { event: 'error', data: {
          code: error instanceof ApiError ? error.code : 'connection_error', message: errorMessage(error, resume),
        } } });
      }
    } finally {
      if (version === lifetime.current) { unlock(); void refreshConversations(); }
    }
  }

  async function send(rawText: string) {
    const text = rawText.trim();
    if (!text || text.length > 2000 || !lock()) return;
    const turnId = crypto.randomUUID();
    const sessionId = stateRef.current.sessionId;
    if (sessionId) attachSession(turnId, sessionId);
    dispatch({ type: 'user_message', text, turnId });
    await consume(turnId, () => source.chat({ user_id: userId, message: text,
      ...(sessionId ? { session_id: sessionId } : {}), ...(viewMode === 'eng' ? { debug: true } : {}) }));
  }

  function pendingTurn(turnId: string) {
    const turn = stateRef.current.turns.at(-1);
    return turn?.id === turnId && turn.status === 'interrupted' && stateRef.current.sessionId ? turn : undefined;
  }
  async function resumeOrder(turnId: string, order: Order) {
    const turn = pendingTurn(turnId);
    if (!turn?.orderPicker?.some(item => item.order_id === order.order_id) || !lock()) return;
    const sessionId = stateRef.current.sessionId!;
    chosenOrderSession.current = sessionId;
    setChosenOrder(order); notice(turnId, { kind: 'selection', text: `选择订单 ${order.order_id}` });
    dispatch({ type: 'resume', turnId });
    await consume(turnId, () => source.resume({ session_id: sessionId, user_id: userId, order_id: order.order_id,
      ...(viewMode === 'eng' ? { debug: true } : {}) }), 'order');
  }
  async function resumeTicket(turnId: string, confirmed: boolean) {
    if (!pendingTurn(turnId)?.ticketPreview || !lock()) return;
    const sessionId = stateRef.current.sessionId!;
    notice(turnId, { kind: 'selection', text: confirmed ? '确认提交' : '取消' });
    dispatch({ type: 'resume', turnId });
    await consume(turnId, () => source.resume({ session_id: sessionId, user_id: userId, ticket_confirm: confirmed,
      ...(viewMode === 'eng' ? { debug: true } : {}) }), 'ticket');
  }

  function clearExtras() {
    setChosenOrder(undefined); chosenOrderSession.current = undefined; setNotices({}); setCompleted({}); completedRef.current = {};
    setTurnSessions({}); turnSessionsRef.current = {}; setHistoryError('');
  }
  function newSession() {
    if (busyRef.current) return;
    historyVersion.current++; setLoadingHistory(false); clearExtras(); dispatch({ type: 'reset' });
  }
  async function loadConversation(id: string) {
    if (busyRef.current) return;
    const version = ++historyVersion.current;
    setLoadingHistory(true); setHistoryError('');
    try {
      const messages = await source.messages(Number(id), userId);
      if (!mounted.current || version !== historyVersion.current || busyRef.current) return;
      clearExtras(); dispatch({ type: 'load_history', sessionId: id, turns: historyTurns(messages) });
    } catch {
      if (mounted.current && version === historyVersion.current) setHistoryError('会话消息加载失败，请重试');
    } finally {
      if (mounted.current && version === historyVersion.current) setLoadingHistory(false);
    }
  }

  async function submitAction(turnId: string, action: Action, perform: (sessionId: string) => Promise<DeskNotice>) {
    const turn = stateRef.current.turns.find(item => item.id === turnId);
    if (!turn?.actions?.some(item => actionKey(item) === actionKey(action)) || completedRef.current[turnId]?.includes(actionKey(action))) return;
    if (turnSessionsRef.current[turnId] !== stateRef.current.sessionId) throw new Error('会话已失效，请重新提问');
    if (!stateRef.current.sessionId || !lock()) throw new Error('当前正在回复中，请稍后再试');
    const version = lifetime.current;
    try {
      const result = await perform(stateRef.current.sessionId);
      if (!mounted.current || version !== lifetime.current) return;
      notice(turnId, result);
      completedRef.current = { ...completedRef.current, [turnId]: [...(completedRef.current[turnId] ?? []), actionKey(action)] };
      setCompleted(completedRef.current);
      void refreshConversations();
    } catch (error) { throw new Error(errorMessage(error)); }
    finally { if (version === lifetime.current) unlock(); }
  }
  async function submitRefund(turnId: string, action: Extract<Action, { type: 'refund' }>, values: Pick<RefundRequest, 'reason' | 'note'>) {
    await submitAction(turnId, action, async sessionId => {
      const result = await source.submitRefund({ session_id: sessionId, user_id: userId, order_id: action.order_id,
        reason: values.reason, ...(values.note?.trim() ? { note: values.note.trim() } : {}) });
      return { kind: 'success', text: `已为您提交退款申请 ${result.refund_no}，状态：${result.status}。` };
    });
  }
  async function submitTicket(turnId: string, action: Extract<Action, { type: 'ticket' }>, description: string) {
    if (!['售后', '投诉', '咨询'].includes(action.ticket_type) || !description.trim() || description.length > 500) throw new Error('请检查工单类型和描述');
    await submitAction(turnId, action, async sessionId => {
      const result = await source.submitTicket({ session_id: sessionId, user_id: userId,
        ticket_type: action.ticket_type as TicketRequest['ticket_type'], description });
      return { kind: 'success', text: `工单已创建：${result.ticket_no}` };
    });
  }
  async function handoff(turnId: string) {
    await submitAction(turnId, { type: 'handoff' }, async () => ({ kind: 'handoff', text: '已转接人工客服' }));
  }

  let selectedOrder: OrderSummary | undefined = chosenOrderSession.current === state.sessionId ? chosenOrder : undefined;
  for (const turn of [...state.turns].reverse()) {
    if (turnSessions[turn.id] !== state.sessionId) continue;
    const refund = turn.actions?.find(action => action.type === 'refund');
    const toolOrder = [...turn.tools].reverse().find(tool => typeof tool.args.order_id === 'string')?.args.order_id;
    const resolved = [...turn.trace].reverse().find(trace => trace.kind === 'resolve');
    const id = refund?.order_id ?? toolOrder ?? (resolved?.kind === 'resolve' ? resolved.data.order_id : undefined);
    if (typeof id === 'string' && id) { selectedOrder = selectedOrder?.order_id === id ? selectedOrder : { order_id: id }; break; }
  }

  return { state, userId, busy, loadingHistory, conversations, listError, historyError, selectedOrder, notices, completed, turnSessions,
    send, resumeOrder, resumeTicket, newSession, loadConversation, refreshConversations, submitRefund, submitTicket, handoff,
    selectTurn: (turnId: string) => dispatch({ type: 'select_turn', turnId }),
  };
}

export type DeskSession = ReturnType<typeof useDeskSession>;
