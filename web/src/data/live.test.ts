// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './DataSource';
import { LiveDataSource } from './live';
import type { DataSource } from './DataSource';
import { collectAsync } from './test-utils';

afterEach(() => vi.unstubAllGlobals());
const chat = { user_id: 'demo', message: '退款', debug: true };
const resume = { user_id: 'demo', session_id: '42', ticket_confirm: false, debug: true };

describe('LiveDataSource', () => {
  it.each([['chat', '/chat/stream', chat], ['resume', '/chat/resume', resume]] as const)
    ('streams %s as named JSON events with the exact POST body', async (method, path, request) => {
      vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
        expect(url).toBe(path);
        expect(init.method).toBe('POST');
        expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
        expect(new Headers(init.headers).get('Accept')).toBe('text/event-stream');
        expect(JSON.parse(init.body as string)).toEqual(request);
        return new Response('event: session\ndata: {"session_id":"42"}\n\nevent: token\ndata: {"text":"您好"}\n\nevent: done\ndata: {"finish_reason":"stop","message_id":9}\n\n');
      });
      expect(await collectAsync(new LiveDataSource()[method](request as typeof resume & typeof chat)))
        .toEqual([{ event: 'session', data: { session_id: '42' } }, { event: 'token', data: { text: '您好' } },
          { event: 'done', data: { finish_reason: 'stop', message_id: 9 } }]);
    });
  it.each([
    [409, { detail: { code: 'session_busy', message: '会话忙' } }, 'session_busy', '会话忙'],
    [422, { detail: [{ loc: ['body', 'user_id'], msg: 'Field required', type: 'missing' },
      { loc: ['body', 'message'], msg: 'Too short', type: 'string_too_short' }] }, 'validation_error', 'body.user_id: Field required; body.message: Too short'],
    [404, { code: 'not_exported' }, 'not_exported', 'HTTP 404'],
    [500, { detail: 'server failed' }, 'http_error', 'server failed'],
  ])('maps HTTP %i errors for both SSE and REST', async (status, body, code, message) => {
    vi.stubGlobal('fetch', async () => Response.json(body, { status }));
    const source = new LiveDataSource();
    for (const action of [() => collectAsync(source.chat(chat)), () => source.evalRuns()]) {
      await expect(action()).rejects.toBeInstanceOf(ApiError);
      await expect(action()).rejects.toMatchObject({ status, code, message });
    }
  });
  it('preserves an HTTP error when the error body is not JSON', async () => {
    vi.stubGlobal('fetch', async () => new Response('<html>Bad gateway</html>', { status: 502 }));
    await expect(new LiveDataSource().toolAudit(10)).rejects.toMatchObject({ status: 502, code: 'http_error', message: 'HTTP 502' });
  });
  it('rejects a successful stream response without a body', async () => {
    vi.stubGlobal('fetch', async () => new Response(null));
    await expect(collectAsync(new LiveDataSource().chat(chat))).rejects.toMatchObject({ code: 'missing_stream' });
  });
  it('ignores unknown SSE event names while preserving protocol errors', async () => {
    vi.stubGlobal('fetch', async () => new Response('event: future\ndata: {}\n\nevent: error\ndata: {"code":"upstream_error","message":"稍后重试"}\n\n'));
    expect(await collectAsync(new LiveDataSource().chat(chat))).toEqual([{ event: 'error', data: { code: 'upstream_error', message: '稍后重试' } }]);
  });
  it('does not parse a 204 feedback response as JSON', async () => {
    vi.stubGlobal('fetch', async () => new Response(null, { status: 204 }));
    await expect(new LiveDataSource().feedback({ user_id: 'demo', conversation_id: 42, message_id: 9, rating: 'up' })).resolves.toBeUndefined();
  });

  type Route = [string, string, unknown, (s: DataSource) => Promise<unknown>, unknown];
  const routes: Route[] = [
    ['/api/conversations?user_id=demo', 'GET', undefined, s => s.conversations('demo'), [{ session_id: '42', preview: '退款', summarized: true, created_at: 'date', updated_at: 'date' }]],
    ['/api/conversations/42/messages?user_id=demo', 'GET', undefined, s => s.messages(42, 'demo'), [{ id: 9, role: 'assistant', content: '您好', created_at: 'date' }]],
    ['/api/knowledge/chunks/3', 'GET', undefined, s => s.knowledgeChunk(3), { id: 3, section_path: null, content_type: null, questions: '退款', answer: '七天', prev_chunk_id: null, next_chunk_id: null }],
    ['/refunds', 'POST', { user_id: 'demo', session_id: '42', order_id: '1001', reason: '七天无理由', note: '未拆封' },
      s => s.submitRefund({ user_id: 'demo', session_id: '42', order_id: '1001', reason: '七天无理由', note: '未拆封' }), { refund_no: 'R123', status: '待审核' }],
    ['/tickets', 'POST', { user_id: 'demo', session_id: '42', description: '耳机坏了', ticket_type: '售后' },
      s => s.submitTicket({ user_id: 'demo', session_id: '42', description: '耳机坏了', ticket_type: '售后' }), { ticket_no: 'T123', status: '待处理' }],
    ['/api/feedback', 'POST', { user_id: 'demo', conversation_id: 42, message_id: 9, rating: 'down' },
      s => s.feedback({ user_id: 'demo', conversation_id: 42, message_id: 9, rating: 'down' }), undefined],
    ['/api/review-queue?status=%E5%BE%85%E5%AE%A1', 'GET', undefined, s => s.review.list('待审'), []],
    ['/api/review-queue/7', 'GET', undefined, s => s.review.detail(7), { id: 7, sources: [] }],
    ['/api/review-queue/7/approve', 'POST', { approved_answer: '七天可退', product_category: '通用' },
      s => s.review.approve(7, { approved_answer: '七天可退', product_category: '通用' }), { chunk_id: 3, vectorized: 1 }],
    ['/api/review-queue/7/reject', 'POST', undefined, s => s.review.reject(7), { id: 7, review_status: '驳回' }],
    ['/api/eval-runs', 'GET', undefined, s => s.evalRuns(), [{ id: 1, triggered_by: '手动', dataset_size: 300, metrics: { recall: 0.9 }, created_at: 'date' }]],
    ['/api/strategy-comparison', 'GET', undefined, s => s.strategyComparison(), { generated_from: { report: 'r', rankings: 'r', git_commit: 'hash' }, metrics: {}, cases: [] }],
    ['/api/faith-cases?status=%E6%9C%AA%E8%A7%A3%E5%86%B3', 'GET', undefined, s => s.faithCases('未解决'), []],
    ['/api/faith-cases/8/resolve', 'POST', { status: '已解决', resolution: '修正知识' },
      s => s.resolveFaithCase(8, { status: '已解决', resolution: '修正知识' }), { id: 8, status: '已解决' }],
    ['/api/tool-audit?limit=12&status=%E8%B6%85%E6%97%B6', 'GET', undefined, s => s.toolAudit(12, '超时'), []],
  ];
  it.each(routes)('uses backend route %s and preserves response fields', async (url, method, body, call, output) => {
    vi.stubGlobal('fetch', async (actualUrl: string, init: RequestInit) => {
      expect(actualUrl).toBe(url);
      expect(init.method).toBe(method);
      expect(init.body === undefined ? undefined : JSON.parse(init.body as string)).toEqual(body);
      return Response.json(output ?? { id: 1, duplicate: false }, { status: 200 });
    });
    expect(await call(new LiveDataSource())).toEqual(output);
  });
});
