import { EVENT_NAMES } from '../protocol/events';
import type {
  AuditStatus, ChatRequest, ConversationSummary, DataSource, EvalRun, FaithCase, FaithStatus,
  FeedbackRequest, KnowledgeChunk, RefundRequest, RefundResult, ResolveFaithRequest, ResumeRequest,
  ReviewApi, Scene, SceneHeader, SceneLine, StoredMessage, StrategyComparison, TicketRequest,
  TicketResult, ToolAuditRow,
} from './DataSource';
import type { ChatEvent } from '../protocol/events';
import { fetchResponse, requestJson } from './live';

export type { Scene, SceneHeader, SceneLine } from './DataSource';

export class ReplayReadOnlyError extends Error {
  constructor() { super('回放模式不可用'); this.name = 'ReplayReadOnlyError'; }
}
function readOnly<T>(): Promise<T> { return Promise.reject(new ReplayReadOnlyError()); }
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export async function loadScene(id: string): Promise<Scene> {
  if (!/^[a-z0-9][a-z0-9-]*$/i.test(id)) throw new Error('Invalid scene id');
  const text = await (await fetchResponse(assetUrl(`/replays/${id}.jsonl`))).text();
  let rows: unknown[];
  try { rows = text.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line)); }
  catch { throw new Error('Invalid recording JSONL'); }
  const header = rows[0];
  const fields = ['scene', 'title_zh', 'title_en', 'recorded_at', 'git_commit', 'model'];
  if (!object(header) || fields.some(key => typeof header[key] !== 'string') || header.scene !== id) {
    throw new Error('Invalid recording header');
  }
  let previous = 0;
  for (const line of rows.slice(1)) {
    if (!object(line) || typeof line.t_ms !== 'number' || !Number.isFinite(line.t_ms) || line.t_ms < previous ||
        !['customer', 'ops'].includes(String(line.lane)) || !['sse', 'api'].includes(String(line.channel)) ||
        typeof line.event !== 'string' || !('data' in line) ||
        (line.channel === 'sse' && (!(EVENT_NAMES as readonly string[]).includes(line.event) || !object(line.data)))) {
      throw new Error('Invalid recording line or timestamp order');
    }
    previous = line.t_ms;
  }
  return { header: header as unknown as SceneHeader, lines: rows.slice(1) as SceneLine[] };
}

function assetUrl(path: string): string {
  return `${import.meta.env.BASE_URL ?? '/'}${path.replace(/^\//, '')}`;
}

// Extra detail snapshots mirror the API path under /snapshots, with a .json suffix.
export class ReplayDataSource implements DataSource {
  readonly mode = 'replay';
  loadScene = loadScene;
  async *chat(_req: ChatRequest): AsyncGenerator<ChatEvent> { throw new ReplayReadOnlyError(); }
  async *resume(_req: ResumeRequest): AsyncGenerator<ChatEvent> { throw new ReplayReadOnlyError(); }
  // A snapshot contains the recorded user's conversations, independent of the live user id.
  conversations(_userId: string): Promise<ConversationSummary[]> { return requestJson(assetUrl('/snapshots/conversations.json')); }
  messages(id: number, _userId: string): Promise<StoredMessage[]> {
    return requestJson(assetUrl(`/snapshots/conversations/${id}/messages.json`));
  }
  knowledgeChunk(id: number): Promise<KnowledgeChunk> { return requestJson(assetUrl(`/snapshots/knowledge/chunks/${id}.json`)); }
  feedback(_req: FeedbackRequest): Promise<void> { return readOnly(); }
  submitRefund(_req: RefundRequest): Promise<RefundResult> { return readOnly(); }
  submitTicket(_req: TicketRequest): Promise<TicketResult> { return readOnly(); }
  readonly review: ReviewApi = {
    list: async status => {
      const rows = await requestJson<Awaited<ReturnType<ReviewApi['list']>>>(assetUrl('/snapshots/review-queue.json'));
      return status === undefined ? rows : rows.filter(row => row.review_status === status);
    },
    detail: id => requestJson(assetUrl(`/snapshots/review-queue/${id}.json`)),
    approve: (_id, _req) => readOnly(),
    reject: _id => readOnly(),
  };
  evalRuns(): Promise<EvalRun[]> { return requestJson(assetUrl('/snapshots/eval-runs.json')); }
  strategyComparison(): Promise<StrategyComparison> { return requestJson(assetUrl('/snapshots/strategy-comparison.json')); }
  async faithCases(status?: FaithStatus): Promise<FaithCase[]> {
    const rows = await requestJson<FaithCase[]>(assetUrl('/snapshots/faith-cases.json'));
    return status === undefined ? rows : rows.filter(row => row.status === status);
  }
  resolveFaithCase(_id: number, _req: ResolveFaithRequest): Promise<FaithCase> { return readOnly(); }
  async toolAudit(limit: number, status?: AuditStatus): Promise<ToolAuditRow[]> {
    const rows = await requestJson<ToolAuditRow[]>(assetUrl('/snapshots/tool-audit.json'));
    return (status === undefined ? rows : rows.filter(row => row.status === status)).slice(0, limit);
  }
}
