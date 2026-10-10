import { describe, expect, it } from 'vitest';
import schema from './events.schema.json';
import { EVENT_NAMES, TRACE_KINDS } from './events';
import type {
  Action, ChatEvent, Citation, Order, RetrievalSnapshot, TicketPreview,
  ToolCall, ToolResult, TraceEvent,
} from './events';

type Fields<T> = { [K in keyof T]-?: {} extends Pick<T, K> ? 'optional' : 'required' };
type Payload<E extends ChatEvent['event']> = Extract<ChatEvent, { event: E }>['data'];
type TracePayload<K extends TraceEvent['kind']> = Extract<TraceEvent, { kind: K }>['data'];

const eventFields = {
  session: { session_id: 'required' },
  understood: { resolved_input: 'required', intent: 'required' },
  token: { text: 'required' },
  tool_start: { tools: 'required' },
  tool_end: { tools: 'required' },
  citations: { items: 'required', refused: 'required' },
  actions: { options: 'required' },
  order_picker: { orders: 'required' },
  ticket_preview: { call_id: 'required', ticket_type: 'required', description: 'required' },
  error: { code: 'required', message: 'required' },
  done: { finish_reason: 'required', message_id: 'optional' },
  trace: { kind: 'required', node: 'required', t_ms: 'required', data: 'required' },
} satisfies { [E in ChatEvent['event']]: Fields<Payload<E>> };

const traceFields = {
  node_start: {},
  node_end: { ms: 'required', interrupted: 'optional' },
  resolve: {
    original: 'required', resolved_input: 'required', standard_query: 'required',
    order_id: 'required', order_scoped: 'required', ticket_request: 'required',
    status_query: 'required', history_recall: 'required',
  },
  intent: { intent: 'required', confidence: 'required', route: 'required', escalated: 'required' },
  retrieval: { queries: 'required', top: 'required', kept: 'required' },
  gate: {
    passed: 'required', confidence: 'required', signals: 'required', weights: 'required',
    threshold: 'required', source: 'required', reason: 'required', self_check: 'required',
  },
  tool: {
    call_id: 'required', name: 'required', source: 'required', mcp_server: 'required',
    status: 'required', retry_count: 'required', duration_ms: 'required', error_message: 'required',
  },
  context: {
    layer1_tokens: 'required', layer1_budget: 'required', layer2_tokens: 'required',
    layer2_budget: 'required', summary_triggered: 'required',
  },
  llm: {
    node: 'required', model: 'required', input_tokens: 'required', output_tokens: 'required',
    cache_read_tokens: 'required', reasoning_tokens: 'required', ms: 'required',
  },
} satisfies { [K in TraceEvent['kind']]: Fields<TracePayload<K>> };

const sharedFields = {
  citation: { n: 'required', chunk_id: 'required', section_path: 'required', question: 'required', answer: 'required' } satisfies Fields<Citation>,
  orderCard: { order_id: 'required', title: 'required', total: 'required', created_at: 'required', status: 'required' } satisfies Fields<Order>,
  toolCall: { id: 'required', name: 'required', args: 'required' } satisfies Fields<ToolCall>,
  toolResult: { id: 'required', name: 'required', ok: 'required' } satisfies Fields<ToolResult>,
  ticket_preview: { call_id: 'required', ticket_type: 'required', description: 'required' } satisfies Fields<TicketPreview>,
  retrievalSnapshot: { chunk_id: 'required', section_path: 'required', question: 'required', answer: 'required', score: 'required' } satisfies Fields<RetrievalSnapshot>,
};

const actionFields = {
  handoff: { type: 'required' },
  ticket: { type: 'required', description: 'required', ticket_type: 'required' },
  refund: { type: 'required', order_id: 'required' },
} satisfies { [K in Action['type']]: Fields<Extract<Action, { type: K }>> };

interface ObjectSchema { properties: object; required: string[] }
function checkFields(fields: Record<string, string>, definition: ObjectSchema) {
  expect(Object.keys(fields).sort()).toEqual(Object.keys(definition.properties).sort());
  expect(Object.keys(fields).filter(key => fields[key] === 'required').sort())
    .toEqual([...definition.required].sort());
}

const definitions = schema.$defs;
function definition(ref: string) {
  return definitions[ref.replace('#/$defs/', '') as keyof typeof definitions];
}

describe('TypeScript / JSON Schema contract', () => {
  it('covers exactly every event name, with no duplicate names', () => {
    const missing: Record<Exclude<ChatEvent['event'], typeof EVENT_NAMES[number]>, never> = {};
    expect(missing).toEqual({});
    expect([...EVENT_NAMES].sort()).toEqual(schema.oneOf.map(item => item.properties.event.const).sort());
    expect(new Set(EVENT_NAMES).size).toBe(EVENT_NAMES.length);
  });

  it('covers every concrete trace kind, including both node timing kinds', () => {
    const missing: Record<Exclude<TraceEvent['kind'], typeof TRACE_KINDS[number]>, never> = {};
    expect(missing).toEqual({});
    const kinds = definitions.trace.oneOf.map(item => {
      const entry = definition(item.$ref);
      if (!('properties' in entry) || !('kind' in entry.properties)) throw new Error(item.$ref);
      return entry.properties.kind.const;
    });
    expect([...TRACE_KINDS].sort()).toEqual(kinds.sort());
    expect(new Set(TRACE_KINDS).size).toBe(TRACE_KINDS.length);
  });

  it.each(schema.oneOf.filter(entry => entry.properties.event.const !== 'trace'))('matches business payload fields for $properties.event.const', entry => {
    const name = entry.properties.event.const as ChatEvent['event'];
    const payload = definition(entry.properties.data.$ref);
    if (!('properties' in payload) || !('required' in payload)) throw new Error(name);
    checkFields(eventFields[name], payload);
  });

  it.each(definitions.trace.oneOf)('matches trace payload fields for $ref', entry => {
    const trace = definition(entry.$ref);
    if (!('properties' in trace) || !('kind' in trace.properties)) throw new Error(entry.$ref);
    const kind = trace.properties.kind.const as TraceEvent['kind'];
    checkFields(eventFields.trace, trace);
    checkFields(traceFields[kind], trace.properties.data);
  });

  it.each(Object.entries(sharedFields))('matches nested %s fields', (name, fields) => {
    const entry = definition(`#/$defs/${name}`);
    if (!('properties' in entry) || !('required' in entry)) throw new Error(name);
    checkFields(fields, entry);
  });

  it.each(definitions.action.oneOf)('matches action fields for $properties.type.const', entry => {
    checkFields(actionFields[entry.properties.type.const as Action['type']], entry);
  });

  it('matches nested gate signal fields', () => {
    const signals = {
      top1: 'required', effective: 'required', margin: 'required',
    } satisfies Fields<TracePayload<'gate'>['signals']>;
    checkFields(signals, definitions.gateTrace.properties.data.properties.signals);
  });
});
