import type { SceneLine } from '../data/DataSource';
import type { DeskNotice, DeskSession } from '../desk/useDeskSession';
import type { DeskAction } from '../state/conversation';
import { replay } from '../state/conversation';

export function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
export function projectRecording(lines: SceneLine[], index: number) {
  const actions: DeskAction[] = [];
  const notices: Record<string, DeskNotice[]> = {};
  const turnSessions: Record<string, string> = {};
  let turnId = '';
  let sessionId = '';
  const notice = (text: string, kind: DeskNotice['kind'] = 'selection') => {
    if (turnId) (notices[turnId] ??= []).push({ kind, text });
  };
  for (const [i, line] of lines.slice(0, index + 1).entries()) {
    if (line.lane !== 'customer') continue;
    if (line.channel === 'api') {
      const data = object(line.data);
      if (line.event === 'chat') {
        turnId = `recorded-${i}`;
        if (typeof data.session_id === 'string') sessionId = data.session_id;
        if (sessionId) turnSessions[turnId] = sessionId;
        actions.push({ type: 'user_message', turnId, text: String(data.message ?? '') });
      } else if (line.event === 'pick_order' || line.event === 'confirm_ticket') {
        notice(line.event === 'pick_order' ? `选择订单 ${data.order_id}` : data.ticket_confirm ? '确认提交' : '取消');
        if (turnId) actions.push({ type: 'resume', turnId });
      } else if (line.event === 'refund') {
        const response = object(data.response);
        notice(`退款申请 ${response.refund_no} · ${response.status}`, 'success');
      }
    } else {
      if (!turnId) { turnId = `recorded-${i}`; actions.push({ type: 'user_message', turnId, text: '' }); }
      if (line.event === 'session') { sessionId = line.data.session_id; turnSessions[turnId] = sessionId; }
      actions.push({ type: 'event', turnId, event: line });
    }
  }
  return { state: replay(actions), notices, turnSessions };
}
const noAction = async () => {};
export function readOnlyDesk(projection: ReturnType<typeof projectRecording>, onSelect: (id: string) => void): DeskSession {
  return {
    ...projection, userId: 'recorded', busy: false, loadingHistory: false, conversations: [], listError: '', historyError: '',
    selectedOrder: undefined, completed: {}, selectTurn: onSelect,
    send: noAction, resumeOrder: noAction, resumeTicket: noAction, newSession: () => {}, loadConversation: noAction,
    refreshConversations: noAction, submitRefund: noAction, submitTicket: noAction, handoff: noAction,
  };
}
