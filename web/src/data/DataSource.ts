import type { ChatEvent } from '../protocol/events';

export interface ChatRequest { session_id?: string | null; user_id: string; message: string; debug?: boolean }
export type ResumeRequest = { session_id: string; user_id: string; debug?: boolean } & (
  | { order_id: string; ticket_confirm?: null }
  | { ticket_confirm: boolean; order_id?: null }
);
export interface FeedbackRequest { conversation_id: number; message_id: number; rating: 'up' | 'down'; user_id: string }
export interface RefundRequest {
  session_id: string; user_id: string; order_id: string;
  reason: '七天无理由' | '质量问题' | '商品与描述不符' | '发错货或漏发' | '物流损坏' | '其他';
  note?: string | null;
}
export interface TicketRequest { session_id: string; user_id: string; description: string; ticket_type: '售后' | '投诉' | '咨询' }
export interface RefundResult { refund_no: string; status: string }
export interface TicketResult { ticket_no: string; status: string }
export interface ConversationSummary {
  session_id: string; created_at: string; updated_at: string; preview: string; summarized: boolean;
}
export interface StoredMessage { id: number; role: 'user' | 'assistant'; content: string; created_at: string }
export interface KnowledgeChunk {
  id: number; section_path: string | null; content_type: string | null; questions: string; answer: string;
  prev_chunk_id: number | null; next_chunk_id: number | null;
}
export type ReviewStatus = '待审' | '通过' | '驳回';
export interface ReviewItem {
  id: number; normalized_question: string; ai_suggested_answer: string | null; occurrence_count: number;
  review_status: ReviewStatus; approved_answer: string | null; created_at: string; updated_at: string;
}
export interface ReviewSource {
  id: number; raw_question: string; source: string; reason: string | null; created_at: string;
  retrieved_chunks: Record<string, unknown>[] | null;
}
export interface ReviewDetail extends ReviewItem { sources: ReviewSource[] }
export interface ApproveRequest { approved_answer: string; product_category?: string }
export interface ReviewApi {
  list(status?: ReviewStatus): Promise<ReviewItem[]>;
  detail(id: number): Promise<ReviewDetail>;
  approve(id: number, req: ApproveRequest): Promise<{ chunk_id: number; vectorized: number }>;
  reject(id: number): Promise<{ id: number; review_status: '驳回' }>;
}
export interface EvalRun { id: number; triggered_by: string; dataset_size: number; metrics: Record<string, unknown>; created_at: string }
export type Strategy = 'dense' | 'bm25' | 'hybrid' | 'hybrid_rerank';
export interface StrategyMetrics {
  'R@1': number; 'R@3': number; 'R@5': number; 'R@10': number; MRR: number;
  faithfulness: number; false_refusal: number; d_refusal: number;
}
export interface StrategyComparison {
  generated_from: { report: string; rankings: string; git_commit: string; metrics_source?: string; consistency_note?: string };
  metrics: Record<Strategy, StrategyMetrics>;
  cases: { id: string; query: string; bucket: string; relevant: string[][];
    rankings: Record<Strategy, { key: string; relevant: boolean }[]>; fallback?: boolean }[];
}
export type FaithStatus = '未解决' | '已解决' | '无需解决';
export interface FaithCase {
  id: number; eval_id: string; bucket: string; query: string; strategy: string; answer: string; reason: string;
  citations: Record<string, unknown>[] | null; cited: number[]; judge_model: string | null; status: FaithStatus;
  seen_count: number; first_seen_at: string; last_seen_at: string; resolution: string | null; resolved_at: string | null;
}
export interface ResolveFaithRequest { status: '已解决' | '无需解决'; resolution: string }
export type AuditStatus = '成功' | '失败' | '超时' | '校验拦下' | '权限拒绝';
export interface ToolAuditRow {
  id: number; created_at: string; conversation_id: number | null; tool_call_id: string | null;
  tool_name: string; tool_source: string; mcp_server: string | null; status: AuditStatus; retry_count: number;
  duration_ms: number | null; error_message: string | null; result_summary: string | null;
}

export interface DataSource {
  readonly mode: 'live' | 'replay';
  chat(req: ChatRequest): AsyncIterable<ChatEvent>;
  resume(req: ResumeRequest): AsyncIterable<ChatEvent>;
  conversations(userId: string): Promise<ConversationSummary[]>;
  messages(conversationId: number, userId: string): Promise<StoredMessage[]>;
  knowledgeChunk(id: number): Promise<KnowledgeChunk>;
  feedback(req: FeedbackRequest): Promise<void>;
  submitRefund(req: RefundRequest): Promise<RefundResult>;
  submitTicket(req: TicketRequest): Promise<TicketResult>;
  readonly review: ReviewApi;
  evalRuns(): Promise<EvalRun[]>;
  strategyComparison(): Promise<StrategyComparison>;
  faithCases(status?: FaithStatus): Promise<FaithCase[]>;
  resolveFaithCase(id: number, req: ResolveFaithRequest): Promise<FaithCase>;
  toolAudit(limit: number, status?: AuditStatus): Promise<ToolAuditRow[]>;
}

export interface SceneHeader {
  scene: string; title_zh: string; title_en: string; recorded_at: string; git_commit: string; model: string;
}
export type SceneLine = { t_ms: number; lane: 'customer' | 'ops' } & (
  | ({ channel: 'sse' } & ChatEvent)
  | { channel: 'api'; event: string; data: unknown }
);
export interface Scene { header: SceneHeader; lines: SceneLine[] }

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}
