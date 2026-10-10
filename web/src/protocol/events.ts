export interface Citation {
  n: number;
  chunk_id: number;
  section_path: string;
  question: string;
  answer: string;
}

export type Action =
  | { type: 'handoff' }
  | { type: 'ticket'; description: string; ticket_type: string }
  | { type: 'refund'; order_id: string };

export interface Order {
  order_id: string;
  title: string;
  total: number;
  created_at: string;
  status: string;
}

export interface ToolCall { id: string; name: string; args: Record<string, unknown> }
export interface ToolResult { id: string; name: string; ok: boolean }
export interface TicketPreview { call_id: string; ticket_type: string; description: string }
export interface RetrievalSnapshot {
  chunk_id: number;
  section_path: string;
  question: string;
  answer: string;
  score: number;
}

type Trace<K extends string, D, N = string> = { kind: K; node: N; t_ms: number; data: D };
export type TraceEvent =
  | Trace<'node_start', Record<string, never>>
  | Trace<'node_end', { ms: number; interrupted?: boolean }>
  | Trace<'resolve', {
      original: string; resolved_input: string; standard_query: string; order_id: string | null;
      order_scoped: boolean; ticket_request: boolean; status_query: boolean; history_recall: boolean;
    }>
  | Trace<'intent', {
      intent: string | null; confidence: number | null;
      route: 'knowledge' | 'aftersales' | 'business' | 'complaint' | 'chitchat'; escalated: boolean;
    }>
  | Trace<'retrieval', { queries: string[]; top: RetrievalSnapshot[]; kept: number }>
  | Trace<'gate', {
      passed: boolean; confidence: number; signals: { top1: number; effective: number; margin: number };
      weights: [number, number, number]; threshold: number;
      source: null | 'retrieval_low_conf' | 'self_check'; reason: string; self_check: boolean;
    }>
  | Trace<'tool', {
      call_id: string; name: string; source: 'builtin' | 'mcp'; mcp_server: string | null;
      status: '成功' | '失败' | '超时' | '校验拦下' | '权限拒绝';
      retry_count: number; duration_ms: number; error_message: string | null;
    }, string | null>
  | Trace<'context', {
      layer1_tokens: number; layer1_budget: number; layer2_tokens: number; layer2_budget: number;
      summary_triggered: boolean;
    }>
  | Trace<'llm', {
      node: string; model: string; input_tokens: number; output_tokens: number;
      cache_read_tokens: number; reasoning_tokens: number; ms: number;
    }>;

export type ChatEvent =
  | { event: 'session'; data: { session_id: string } }
  | { event: 'understood'; data: { resolved_input: string; intent: string | null } }
  | { event: 'token'; data: { text: string } }
  | { event: 'tool_start'; data: { tools: ToolCall[] } }
  | { event: 'tool_end'; data: { tools: ToolResult[] } }
  | { event: 'citations'; data: { items: Citation[]; refused: boolean } }
  | { event: 'actions'; data: { options: Action[] } }
  | { event: 'order_picker'; data: { orders: Order[] } }
  | { event: 'ticket_preview'; data: TicketPreview }
  | { event: 'error'; data: { code: string; message: string } }
  | { event: 'done'; data: { finish_reason: 'stop' | 'interrupted'; message_id?: number } }
  | { event: 'trace'; data: TraceEvent };

export const EVENT_NAMES = [
  'session', 'understood', 'token', 'tool_start', 'tool_end', 'citations',
  'actions', 'order_picker', 'ticket_preview', 'error', 'done', 'trace',
] as const satisfies readonly ChatEvent['event'][];

export const TRACE_KINDS = [
  'node_start', 'node_end', 'resolve', 'intent', 'retrieval', 'gate', 'tool', 'context', 'llm',
] as const satisfies readonly TraceEvent['kind'][];
