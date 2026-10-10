import { describe, expect, it } from 'vitest';
import type { ChatEvent, TraceEvent } from '../protocol/events';
import { reduce, replay } from './conversation';
import type { DeskAction, DeskState, Turn } from './conversation';

const initial: DeskState = { turns: [] };
const user: DeskAction = { type: 'user_message', text: '它怎么保养？', turnId: 'turn-1' };
const turn: Turn = {
  id: 'turn-1', userText: '它怎么保养？', replyText: '', status: 'streaming', tools: [], trace: [],
};
const started: DeskState = { turns: [turn], activeTurnId: 'turn-1' };
const citation = { n: 1, chunk_id: 7, section_path: 'X3 Pro/保养', question: '', answer: '清洁滤网。' };
const order = { order_id: '1001', title: 'X3 Pro', total: 299, created_at: '2026-10-01', status: '已签收' };
const preview = { call_id: 'write-1', ticket_type: '售后', description: '滤网损坏' };
const tool = { id: 'call-1', name: 'query_order', args: { order_id: '1001' } };
function event(event: ChatEvent, turnId = 'turn-1'): DeskAction {
  return { type: 'event', turnId, event };
}
function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

const traces: TraceEvent[] = [
  { kind: 'node_start', node: 'resolve_reference', t_ms: 0, data: {} },
  { kind: 'node_end', node: 'ensure_order', t_ms: 2, data: { ms: 2, interrupted: true } },
  { kind: 'resolve', node: 'resolve_reference', t_ms: 10, data: {
    original: '它怎么保养？', resolved_input: 'X3 Pro 怎么保养？', standard_query: 'X3 Pro 保养',
    order_id: null, order_scoped: false, ticket_request: false, status_query: false, history_recall: false,
  } },
  { kind: 'intent', node: 'classify_intent', t_ms: 20, data: { intent: '知识咨询', confidence: 0.95, route: 'knowledge', escalated: false } },
  { kind: 'retrieval', node: 'retrieve', t_ms: 30, data: { queries: ['X3 Pro 保养'], top: [{ chunk_id: 7, section_path: 'X3 Pro/保养', question: '', answer: '清洁滤网。', score: 0.9 }], kept: 1 } },
  { kind: 'gate', node: 'confidence_gate', t_ms: 40, data: { passed: true, confidence: 0.9, signals: { top1: 0.9, effective: 0.1, margin: 0.9 }, weights: [0.7, 0.2, 0.1], threshold: 0.6, source: null, reason: '', self_check: true } },
  { kind: 'tool', node: null, t_ms: 50, data: { call_id: 'call-1', name: 'query_logistics', source: 'mcp', mcp_server: 'logistics', status: '成功', retry_count: 0, duration_ms: 10, error_message: null } },
  { kind: 'context', node: 'finalize', t_ms: 60, data: { layer1_tokens: 120, layer1_budget: 3604, layer2_tokens: 0, layer2_budget: 1545, summary_triggered: false } },
  { kind: 'llm', node: 'agent_model', t_ms: 70, data: { node: 'agent_model', model: 'demo', input_tokens: 200, output_tokens: 20, cache_read_tokens: 50, reasoning_tokens: 10, ms: 15 } },
];

const eventCases: { event: ChatEvent; before?: DeskState; expected: DeskState }[] = [
  { event: { event: 'session', data: { session_id: '42' } }, expected: { ...started, sessionId: '42' } },
  { event: { event: 'understood', data: { resolved_input: 'X3 Pro 怎么保养？', intent: null } }, expected: { ...started, turns: [{ ...turn, understood: { resolved_input: 'X3 Pro 怎么保养？', intent: null } }] } },
  { event: { event: 'token', data: { text: '清洁滤网。' } }, expected: { ...started, turns: [{ ...turn, replyText: '清洁滤网。' }] } },
  { event: { event: 'tool_start', data: { tools: [tool] } }, expected: { ...started, turns: [{ ...turn, tools: [tool] }] } },
  { event: { event: 'tool_end', data: { tools: [{ id: tool.id, name: tool.name, ok: false }] } }, before: { ...started, turns: [{ ...turn, tools: [tool] }] }, expected: { ...started, turns: [{ ...turn, tools: [{ ...tool, ok: false }] }] } },
  { event: { event: 'citations', data: { items: [citation], refused: true } }, expected: { ...started, turns: [{ ...turn, citations: [citation], refused: true }] } },
  { event: { event: 'actions', data: { options: [{ type: 'handoff' }, { type: 'ticket', description: '滤网损坏', ticket_type: '售后' }, { type: 'refund', order_id: '1001' }] } }, expected: { ...started, turns: [{ ...turn, actions: [{ type: 'handoff' }, { type: 'ticket', description: '滤网损坏', ticket_type: '售后' }, { type: 'refund', order_id: '1001' }] }] } },
  { event: { event: 'order_picker', data: { orders: [order] } }, expected: { ...started, turns: [{ ...turn, status: 'interrupted', orderPicker: [order] }] } },
  { event: { event: 'ticket_preview', data: preview }, expected: { ...started, turns: [{ ...turn, status: 'interrupted', ticketPreview: preview }] } },
  { event: { event: 'error', data: { code: 'upstream_error', message: '服务不可用' } }, expected: { ...started, turns: [{ ...turn, status: 'error', error: { code: 'upstream_error', message: '服务不可用' } }] } },
  { event: { event: 'done', data: { finish_reason: 'stop', message_id: 99 } }, expected: { ...started, turns: [{ ...turn, status: 'done', messageId: 99 }] } },
  { event: { event: 'trace', data: traces[0] }, expected: { ...started, turns: [{ ...turn, trace: [traces[0]] }] } },
];

const knowledgeActions: DeskAction[] = [
  user,
  event({ event: 'session', data: { session_id: '42' } }),
  event({ event: 'trace', data: traces[0] }),
  event({ event: 'trace', data: traces[2] }),
  event({ event: 'understood', data: { resolved_input: 'X3 Pro 怎么保养？', intent: '知识咨询' } }),
  event({ event: 'trace', data: traces[3] }),
  event({ event: 'trace', data: traces[4] }),
  event({ event: 'trace', data: traces[5] }),
  event({ event: 'citations', data: { items: [citation], refused: false } }),
  event({ event: 'token', data: { text: '清洁' } }),
  event({ event: 'token', data: { text: '滤网。[1]' } }),
  event({ event: 'trace', data: traces[8] }),
  event({ event: 'trace', data: traces[7] }),
  event({ event: 'done', data: { finish_reason: 'stop', message_id: 99 } }),
];

describe('reduce', () => {
  it('opens a streaming turn for the user message', () => {
    expect(reduce(initial, user)).toEqual(started);
  });

  it.each(eventCases)('handles $event.event', ({ event: message, expected, ...fixture }) => {
    expect(reduce(fixture.before ?? started, event(message))).toEqual(expected);
  });

  it.each(traces)('appends trace kind $kind in arrival order', trace => {
    const state: DeskState = { ...started, turns: [{ ...turn, trace: [traces[0]] }] };
    expect(reduce(state, event({ event: 'trace', data: trace })).turns[0].trace).toEqual([traces[0], trace]);
  });

  it('keeps an interrupted done event interrupted without a message id', () => {
    const state = reduce(started, event({ event: 'order_picker', data: { orders: [order] } }));
    const result = reduce(state, event({ event: 'done', data: { finish_reason: 'interrupted' } }));
    expect(result.turns[0]).toEqual({ ...turn, status: 'interrupted', orderPicker: [order] });
    expect(Object.hasOwn(result.turns[0], 'messageId')).toBe(false);
  });

  it('does not turn an error into success on a later done', () => {
    const failed = reduce(started, event({ event: 'error', data: { code: 'bad', message: '失败' } }));
    expect(reduce(failed, event({ event: 'done', data: { finish_reason: 'stop' } })).turns[0])
      .toEqual({ ...turn, status: 'error', error: { code: 'bad', message: '失败' } });
  });

  it('allows a successful done without a stored message id', () => {
    expect(reduce(started, event({ event: 'done', data: { finish_reason: 'stop' } })).turns[0])
      .toEqual({ ...turn, status: 'done' });
  });

  it.each([
    { event: 'order_picker', data: { orders: [order] } },
    { event: 'ticket_preview', data: preview },
  ] satisfies ChatEvent[])('resumes $event and appends the reply to the same turn', card => {
    const actions: DeskAction[] = [user,
      event({ event: 'token', data: { text: '请确认。' } }),
      event({ event: 'tool_start', data: { tools: [tool] } }),
      event({ event: 'trace', data: traces[0] }),
      event(card), event({ event: 'done', data: { finish_reason: 'interrupted' } }),
      { type: 'resume', turnId: 'turn-1' },
      event({ event: 'session', data: { session_id: '42' } }),
      event({ event: 'token', data: { text: '已办理。' } }),
      event({ event: 'done', data: { finish_reason: 'stop', message_id: 101 } }),
    ];
    const resumed = replay(actions, 7);
    expect(resumed.turns).toEqual([{ ...turn, replyText: '请确认。', tools: [tool], trace: [traces[0]] }]);
    expect(replay(actions)).toEqual({ sessionId: '42', activeTurnId: 'turn-1', turns: [{
      ...turn, replyText: '请确认。已办理。', status: 'done', messageId: 101, tools: [tool], trace: [traces[0]],
    }] });
  });

  it('pairs tool results by id across batches, preserving arguments and failures', () => {
    const second = { id: 'call-2', name: tool.name, args: { order_id: '1002' } };
    const actions = [user,
      event({ event: 'tool_start', data: { tools: [tool] } }),
      event({ event: 'tool_start', data: { tools: [second] } }),
      event({ event: 'tool_end', data: { tools: [
        { id: second.id, name: second.name, ok: false },
        { id: tool.id, name: tool.name, ok: true },
        { id: 'unknown', name: 'query_order', ok: true },
      ] } }),
    ];
    expect(replay(actions).turns[0].tools).toEqual([{ ...tool, ok: true }, { ...second, ok: false }]);
  });

  it('replaces actions and citations, including empty lists and refused=false', () => {
    const actions = [user,
      event({ event: 'actions', data: { options: [{ type: 'handoff' }] } }),
      event({ event: 'actions', data: { options: [] } }),
      event({ event: 'citations', data: { items: [citation], refused: true } }),
      event({ event: 'citations', data: { items: [], refused: false } }),
    ];
    expect(replay(actions).turns[0]).toEqual({ ...turn, actions: [], citations: [], refused: false });
  });

  it('stores understood separately from the answer, even when it matches the input', () => {
    const understood = { resolved_input: turn.userText, intent: '知识咨询' };
    expect(reduce(started, event({ event: 'understood', data: understood })).turns[0])
      .toEqual({ ...turn, understood });
  });

  it('selects a turn without changing where events are delivered', () => {
    const state = reduce(started, { type: 'user_message', text: '第二问', turnId: 'turn-2' });
    const selected = reduce(state, { type: 'select_turn', turnId: 'turn-1' });
    expect(selected.activeTurnId).toBe('turn-1');
    const updated = reduce(selected, event({ event: 'token', data: { text: '第二答' } }, 'turn-2'));
    expect(updated.activeTurnId).toBe('turn-1');
    expect(updated.turns.map(item => item.replyText)).toEqual(['', '第二答']);
  });

  it('loads normalized history and selects its last turn', () => {
    const historical: Turn = { ...turn, status: 'done', replyText: '历史答复', messageId: 12 };
    expect(reduce(started, { type: 'load_history', sessionId: '88', turns: [historical] }))
      .toEqual({ sessionId: '88', turns: [historical], activeTurnId: 'turn-1' });
    expect(reduce(started, { type: 'load_history', sessionId: '89', turns: [] }))
      .toEqual({ sessionId: '89', turns: [] });
  });

  it('ignores missing turn ids for event, resume and selection', () => {
    for (const action of [event({ event: 'token', data: { text: '丢弃' } }, 'missing'),
      { type: 'resume', turnId: 'missing' }, { type: 'select_turn', turnId: 'missing' }] satisfies DeskAction[]) {
      expect(reduce(started, action)).toEqual(started);
    }
  });

  it.each(eventCases)('does not mutate deeply frozen state or $event.event action', ({ event: message, ...fixture }) => {
    const state = deepFreeze(structuredClone(fixture.before ?? started));
    const action = deepFreeze(event(structuredClone(message)));
    const beforeState = structuredClone(state);
    const beforeAction = structuredClone(action);
    reduce(state, action);
    expect(state).toEqual(beforeState);
    expect(action).toEqual(beforeAction);
  });

  it('reduces an entire sequence with frozen inputs and preserves every earlier state', () => {
    const actions: DeskAction[] = [...knowledgeActions, ...eventCases.map(item => event(item.event)),
      { type: 'resume', turnId: 'turn-1' },
      { type: 'user_message', text: '第二问', turnId: 'turn-2' },
      { type: 'select_turn', turnId: 'turn-1' },
      { type: 'load_history', sessionId: '88', turns: [{ ...turn, status: 'done' }] },
    ];
    let state = deepFreeze(structuredClone(initial));
    for (const action of actions) {
      const frozenAction = deepFreeze(structuredClone(action));
      const beforeState = structuredClone(state);
      const beforeAction = structuredClone(frozenAction);
      const next = reduce(state, frozenAction);
      expect(state).toEqual(beforeState);
      expect(frozenAction).toEqual(beforeAction);
      state = deepFreeze(next);
    }
  });
});

describe('replay', () => {
  it('captures a complete knowledge round with trace', () => {
    expect(replay(knowledgeActions)).toMatchInlineSnapshot(`
      {
        "activeTurnId": "turn-1",
        "sessionId": "42",
        "turns": [
          {
            "citations": [
              {
                "answer": "清洁滤网。",
                "chunk_id": 7,
                "n": 1,
                "question": "",
                "section_path": "X3 Pro/保养",
              },
            ],
            "id": "turn-1",
            "messageId": 99,
            "refused": false,
            "replyText": "清洁滤网。[1]",
            "status": "done",
            "tools": [],
            "trace": [
              {
                "data": {},
                "kind": "node_start",
                "node": "resolve_reference",
                "t_ms": 0,
              },
              {
                "data": {
                  "history_recall": false,
                  "order_id": null,
                  "order_scoped": false,
                  "original": "它怎么保养？",
                  "resolved_input": "X3 Pro 怎么保养？",
                  "standard_query": "X3 Pro 保养",
                  "status_query": false,
                  "ticket_request": false,
                },
                "kind": "resolve",
                "node": "resolve_reference",
                "t_ms": 10,
              },
              {
                "data": {
                  "confidence": 0.95,
                  "escalated": false,
                  "intent": "知识咨询",
                  "route": "knowledge",
                },
                "kind": "intent",
                "node": "classify_intent",
                "t_ms": 20,
              },
              {
                "data": {
                  "kept": 1,
                  "queries": [
                    "X3 Pro 保养",
                  ],
                  "top": [
                    {
                      "answer": "清洁滤网。",
                      "chunk_id": 7,
                      "question": "",
                      "score": 0.9,
                      "section_path": "X3 Pro/保养",
                    },
                  ],
                },
                "kind": "retrieval",
                "node": "retrieve",
                "t_ms": 30,
              },
              {
                "data": {
                  "confidence": 0.9,
                  "passed": true,
                  "reason": "",
                  "self_check": true,
                  "signals": {
                    "effective": 0.1,
                    "margin": 0.9,
                    "top1": 0.9,
                  },
                  "source": null,
                  "threshold": 0.6,
                  "weights": [
                    0.7,
                    0.2,
                    0.1,
                  ],
                },
                "kind": "gate",
                "node": "confidence_gate",
                "t_ms": 40,
              },
              {
                "data": {
                  "cache_read_tokens": 50,
                  "input_tokens": 200,
                  "model": "demo",
                  "ms": 15,
                  "node": "agent_model",
                  "output_tokens": 20,
                  "reasoning_tokens": 10,
                },
                "kind": "llm",
                "node": "agent_model",
                "t_ms": 70,
              },
              {
                "data": {
                  "layer1_budget": 3604,
                  "layer1_tokens": 120,
                  "layer2_budget": 1545,
                  "layer2_tokens": 0,
                  "summary_triggered": false,
                },
                "kind": "context",
                "node": "finalize",
                "t_ms": 60,
              },
            ],
            "understood": {
              "intent": "知识咨询",
              "resolved_input": "X3 Pro 怎么保养？",
            },
            "userText": "它怎么保养？",
          },
        ],
      }
    `);
  });

  it('equals sequential reduction of each prefix k=0..n', () => {
    const actions: DeskAction[] = [...knowledgeActions,
      { type: 'user_message', text: '我要退货', turnId: 'turn-2' },
      event({ event: 'order_picker', data: { orders: [order] } }, 'turn-2'),
      event({ event: 'done', data: { finish_reason: 'interrupted' } }, 'turn-2'),
      { type: 'select_turn', turnId: 'turn-1' },
      { type: 'resume', turnId: 'turn-2' },
      event({ event: 'token', data: { text: '已恢复' } }, 'turn-2'),
      event({ event: 'error', data: { code: 'upstream_error', message: '失败' } }, 'turn-2'),
      { type: 'load_history', sessionId: '88', turns: [{ ...turn, status: 'done' }] },
    ];
    deepFreeze(actions);
    for (let k = 0; k <= actions.length; k++) {
      const expected = actions.slice(0, k).reduce(reduce, { turns: [] });
      expect(replay(actions, k), `prefix ${k}`).toEqual(expected);
    }
    expect(replay(actions)).toEqual(replay(actions, actions.length));
    expect(replay([])).toEqual({ turns: [] });
  });
});
