import type { Action, ChatEvent, Citation, Order, TicketPreview, ToolCall, TraceEvent } from '../protocol/events';

export type ToolCallState = ToolCall & { ok?: boolean };
export interface Turn {
  id: string;
  userText: string;
  replyText: string;
  status: 'streaming' | 'done' | 'interrupted' | 'error';
  messageId?: number;
  understood?: Extract<ChatEvent, { event: 'understood' }>['data'];
  citations?: Citation[];
  refused?: boolean;
  actions?: Action[];
  orderPicker?: Order[];
  ticketPreview?: TicketPreview;
  tools: ToolCallState[];
  trace: TraceEvent[];
  error?: Extract<ChatEvent, { event: 'error' }>['data'];
}

export interface DeskState { sessionId?: string; turns: Turn[]; activeTurnId?: string }
export type DeskAction =
  | { type: 'user_message'; text: string; turnId: string }
  | { type: 'resume'; turnId: string }
  | { type: 'event'; turnId: string; event: ChatEvent }
  | { type: 'select_turn'; turnId: string }
  | { type: 'load_history'; sessionId: string; turns: Turn[] };

function applyEvent(turn: Turn, event: ChatEvent): Turn {
  switch (event.event) {
    case 'session':
      return turn;
    case 'understood':
      return { ...turn, understood: event.data };
    case 'token':
      return { ...turn, replyText: turn.replyText + event.data.text };
    case 'tool_start':
      return { ...turn, tools: [...turn.tools, ...event.data.tools] };
    case 'tool_end': {
      const results = new Map(event.data.tools.map(result => [result.id, result]));
      return { ...turn, tools: turn.tools.map(tool => {
        const result = results.get(tool.id);
        return result ? { ...tool, ok: result.ok } : tool;
      }) };
    }
    case 'citations':
      return { ...turn, citations: event.data.items, refused: event.data.refused };
    case 'actions':
      return { ...turn, actions: event.data.options };
    case 'order_picker':
      return { ...turn, status: 'interrupted', orderPicker: event.data.orders };
    case 'ticket_preview':
      return { ...turn, status: 'interrupted', ticketPreview: event.data };
    case 'error':
      return { ...turn, status: 'error', error: event.data };
    case 'done': {
      if (turn.status === 'error') return turn;
      const done: Turn = {
        ...turn, status: event.data.finish_reason === 'interrupted' ? 'interrupted' : 'done',
      };
      if (done.status === 'done' && event.data.message_id !== undefined) {
        done.messageId = event.data.message_id;
      } else {
        delete done.messageId;
      }
      return done;
    }
    case 'trace':
      return { ...turn, trace: [...turn.trace, event.data] };
  }
}

export function reduce(state: DeskState, action: DeskAction): DeskState {
  switch (action.type) {
    case 'user_message':
      return { ...state, activeTurnId: action.turnId, turns: [...state.turns, {
        id: action.turnId, userText: action.text, replyText: '', status: 'streaming', tools: [], trace: [],
      }] };
    case 'load_history':
      return {
        sessionId: action.sessionId,
        turns: [...action.turns],
        ...(action.turns.length ? { activeTurnId: action.turns[action.turns.length - 1].id } : {}),
      };
    case 'select_turn':
      return state.turns.some(turn => turn.id === action.turnId)
        ? { ...state, activeTurnId: action.turnId } : state;
    case 'resume': {
      if (!state.turns.some(turn => turn.id === action.turnId)) return state;
      return { ...state, activeTurnId: action.turnId, turns: state.turns.map(turn => {
        if (turn.id !== action.turnId) return turn;
        const resumed: Turn = { ...turn, status: 'streaming' };
        delete resumed.orderPicker;
        delete resumed.ticketPreview;
        delete resumed.error;
        delete resumed.messageId;
        return resumed;
      }) };
    }
    case 'event':
      // 事件按 turnId 归属；activeTurnId 只控制界面选中轮。
      if (action.event.event === 'session') {
        return { ...state, sessionId: action.event.data.session_id };
      }
      return {
        ...state,
        turns: state.turns.map(turn => turn.id === action.turnId ? applyEvent(turn, action.event) : turn),
      };
  }
}

/** upTo 是动作数量，前缀区间为 [0, upTo)。 */
export function replay(actions: readonly DeskAction[], upTo = actions.length): DeskState {
  return actions.slice(0, upTo).reduce(reduce, { turns: [] });
}
