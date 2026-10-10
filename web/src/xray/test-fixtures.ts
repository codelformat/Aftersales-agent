import type { TraceEvent } from '../protocol/events';
import type { Turn } from '../state/conversation';
export const start = (node: string, t_ms: number): TraceEvent => ({ kind: 'node_start', node, t_ms, data: {} });
export const end = (node: string, t_ms: number, ms: number, interrupted = false): TraceEvent => ({ kind: 'node_end', node, t_ms, data: { ms, interrupted } });
export const resolve: Extract<TraceEvent, { kind: 'resolve' }> = { kind: 'resolve', node: 'resolve_reference', t_ms: 10, data: {
  original: '它能退吗', resolved_input: '空气净化器能退吗', standard_query: '空气净化器退款政策', order_id: '1001',
  order_scoped: true, ticket_request: false, status_query: false, history_recall: true,
} };
export const intent: Extract<TraceEvent, { kind: 'intent' }> = { kind: 'intent', node: 'classify_intent', t_ms: 30,
  data: { intent: '退款退货', confidence: .8, route: 'aftersales', escalated: true } };
export const retrieval: Extract<TraceEvent, { kind: 'retrieval' }> = { kind: 'retrieval', node: 'retrieve_multi', t_ms: 60, data: {
  queries: ['退款政策', '退货期限'], kept: 1, top: [
    { chunk_id: 7, section_path: '退款政策/期限', question: '多久退款？', answer: '三个工作日', score: .9 },
    { chunk_id: 8, section_path: '保修政策', question: '保修多久？', answer: '一年', score: .1 },
  ],
} };
export const gate: Extract<TraceEvent, { kind: 'gate' }> = { kind: 'gate', node: 'confidence_gate', t_ms: 80, data: {
  passed: true, confidence: .62, signals: { top1: .8, effective: .6, margin: .2 }, weights: [.5, .3, .2],
  threshold: .55, source: null, reason: '证据充分', self_check: true,
} };
export const llm: Extract<TraceEvent, { kind: 'llm' }> = { kind: 'llm', node: 'agent_model', t_ms: 100, data: {
  node: 'agent_model', model: 'fixture-model', input_tokens: 120, output_tokens: 50, cache_read_tokens: 80, reasoning_tokens: 20, ms: 30,
} };
export const tool: Extract<TraceEvent, { kind: 'tool' }> = { kind: 'tool', node: 'agent_tools', t_ms: 140, data: {
  call_id: 'c1', name: 'query_logistics', source: 'mcp', mcp_server: 'logistics', status: '超时', retry_count: 2,
  duration_ms: 40, error_message: '连接超时',
} };
export const context: Extract<TraceEvent, { kind: 'context' }> = { kind: 'context', node: 'finalize', t_ms: 170, data: {
  layer1_tokens: 900, layer1_budget: 1000, layer2_tokens: 200, layer2_budget: 500, summary_triggered: true,
} };
export const fullTrace: TraceEvent[] = [start('resolve_reference', 0), resolve, end('resolve_reference', 20, 20),
  start('classify_intent', 20), intent, end('classify_intent', 40, 20), start('retrieve_multi', 40), retrieval,
  end('retrieve_multi', 70, 30), start('confidence_gate', 70), gate, end('confidence_gate', 90, 20),
  start('agent_model', 90), llm, end('agent_model', 120, 30), start('agent_tools', 120), tool,
  end('agent_tools', 160, 40), start('finalize', 160), context, end('finalize', 180, 20)];
export const turn: Turn = { id: 't1', userText: '它能退吗', replyText: '可以 [1]', status: 'done', tools: [], trace: fullTrace,
  citations: [{ n: 1, chunk_id: 7, section_path: '退款政策/期限', question: '多久退款？', answer: '三个工作日' }] };
