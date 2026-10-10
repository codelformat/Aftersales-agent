import { EVENT_NAMES } from '../protocol/events';
import type { ChatEvent } from '../protocol/events';
import { ApiError } from './DataSource';
import type {
  AuditStatus, ChatRequest, ConversationSummary, DataSource, EvalRun, FaithCase, FaithStatus,
  FeedbackRequest, KnowledgeChunk, RefundRequest, RefundResult, ResolveFaithRequest, ResumeRequest,
  ReviewApi, StoredMessage, StrategyComparison, TicketRequest, TicketResult, ToolAuditRow,
} from './DataSource';
import { parseSse } from './sse';

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export async function fetchResponse(url: string, init: RequestInit = { method: 'GET' }): Promise<Response> {
  const response = await fetch(url, init);
  if (response.ok) return response;
  let body: unknown;
  try { body = await response.json(); } catch { body = undefined; }
  const detail = isObject(body) ? body.detail : undefined;
  const structured = isObject(detail) ? detail : isObject(body) ? body : {};
  let message = typeof structured.message === 'string' ? structured.message : `HTTP ${response.status}`;
  let code = typeof structured.code === 'string' ? structured.code : 'http_error';
  if (typeof detail === 'string') message = detail;
  if (Array.isArray(detail)) {
    code = 'validation_error';
    const issues = detail.filter(isObject).map(issue => {
      const location = Array.isArray(issue.loc) ? issue.loc.join('.') : '';
      const text = typeof issue.msg === 'string' ? issue.msg : 'Invalid value';
      return location ? `${location}: ${text}` : text;
    });
    if (issues.length) message = issues.join('; ');
  }
  throw new ApiError(response.status, code, message);
}

export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetchResponse(url, init);
  return response.status === 204 ? undefined as T : await response.json() as T;
}

function post(body?: unknown): RequestInit {
  return body === undefined ? { method: 'POST' } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  };
}
function statusQuery(status?: string): string {
  return status === undefined ? '' : `?status=${encodeURIComponent(status)}`;
}

export class LiveDataSource implements DataSource {
  readonly mode = 'live';

  private async *stream(url: string, req: ChatRequest | ResumeRequest): AsyncGenerator<ChatEvent> {
    const response = await fetchResponse(url, {
      ...post(req), headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    });
    if (!response.body) throw new ApiError(response.status, 'missing_stream', 'Response has no SSE body');
    for await (const frame of parseSse(response.body)) {
      if (!(EVENT_NAMES as readonly string[]).includes(frame.event)) continue;
      yield { event: frame.event, data: JSON.parse(frame.data) } as ChatEvent;
    }
  }

  chat(req: ChatRequest): AsyncIterable<ChatEvent> { return this.stream('/chat/stream', req); }
  resume(req: ResumeRequest): AsyncIterable<ChatEvent> { return this.stream('/chat/resume', req); }
  conversations(userId: string): Promise<ConversationSummary[]> {
    return requestJson(`/api/conversations?user_id=${encodeURIComponent(userId)}`);
  }
  messages(conversationId: number, userId: string): Promise<StoredMessage[]> {
    return requestJson(`/api/conversations/${conversationId}/messages?user_id=${encodeURIComponent(userId)}`);
  }
  knowledgeChunk(id: number): Promise<KnowledgeChunk> { return requestJson(`/api/knowledge/chunks/${id}`); }
  async feedback(req: FeedbackRequest): Promise<void> { await fetchResponse('/api/feedback', post(req)); }
  submitRefund(req: RefundRequest): Promise<RefundResult> { return requestJson('/refunds', post(req)); }
  submitTicket(req: TicketRequest): Promise<TicketResult> { return requestJson('/tickets', post(req)); }
  readonly review: ReviewApi = {
    list: status => requestJson(`/api/review-queue${statusQuery(status)}`),
    detail: id => requestJson(`/api/review-queue/${id}`),
    approve: (id, req) => requestJson(`/api/review-queue/${id}/approve`, post(req)),
    reject: id => requestJson(`/api/review-queue/${id}/reject`, post()),
  };
  evalRuns(): Promise<EvalRun[]> { return requestJson('/api/eval-runs'); }
  strategyComparison(): Promise<StrategyComparison> { return requestJson('/api/strategy-comparison'); }
  faithCases(status?: FaithStatus): Promise<FaithCase[]> {
    return requestJson(`/api/faith-cases${statusQuery(status)}`);
  }
  resolveFaithCase(id: number, req: ResolveFaithRequest): Promise<FaithCase> {
    return requestJson(`/api/faith-cases/${id}/resolve`, post(req));
  }
  toolAudit(limit: number, status?: AuditStatus): Promise<ToolAuditRow[]> {
    return requestJson(`/api/tool-audit?limit=${limit}${status === undefined ? '' : `&status=${encodeURIComponent(status)}`}`);
  }
}
