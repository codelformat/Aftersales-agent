// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ReplayDataSource, ReplayReadOnlyError, loadScene } from './replay';
import fixture from './fixtures/refund.jsonl?raw';
import { collectAsync } from './test-utils';

afterEach(() => vi.unstubAllGlobals());

describe('ReplayDataSource', () => {
  it('loads the real JSONL fixture with header and both lanes/channels intact', async () => {
    vi.stubGlobal('fetch', async (url: string) => {
      expect(url).toBe('/replays/refund.jsonl');
      return new Response(fixture.replaceAll('\n', '\r\n') + '\r\n');
    });
    const scene = await loadScene('refund');
    expect(scene.header).toEqual({ scene: 'refund', title_zh: '退款演示', title_en: 'Refund demo',
      recorded_at: '2026-10-09T12:00:00Z', git_commit: 'test-fixture', model: 'fixture-model' });
    expect(scene.lines).toHaveLength(5);
    expect(scene.lines[0]).toEqual({ t_ms: 0, lane: 'customer', channel: 'api', event: 'chat', data: { user_id: 'demo', message: '我想退款' } });
    expect(scene.lines[2]).toEqual({ t_ms: 1000, lane: 'customer', channel: 'sse', event: 'token', data: { text: '请选择订单' } });
    expect(scene.lines[3]).toMatchObject({ t_ms: 7000, lane: 'ops', channel: 'api' });
    expect(await new ReplayDataSource().loadScene('refund')).toEqual(scene);
  });
  it.each(['', '{"scene":"refund"}\n', fixture.replace('"t_ms":1000', '"t_ms":-1'),
    fixture.replace('"t_ms":7000', '"t_ms":500'), fixture.replace('"lane":"ops"', '"lane":"other"')])
    ('rejects invalid recordings rather than passing bad data to the clock', async text => {
      vi.stubGlobal('fetch', async () => new Response(text));
      await expect(loadScene('refund')).rejects.toThrow(/recording/i);
    });
  it('surfaces missing recordings and snapshots as ApiError', async () => {
    vi.stubGlobal('fetch', async () => new Response('missing', { status: 404 }));
    await expect(loadScene('refund')).rejects.toMatchObject({ status: 404 });
    await expect(new ReplayDataSource().evalRuns()).rejects.toMatchObject({ status: 404 });
  });
  it('rejects scene path traversal before fetching', async () => {
    vi.stubGlobal('fetch', () => { throw new Error('must not fetch'); });
    await expect(loadScene('../secret')).rejects.toThrow(/scene/i);
  });
  it('reads ops snapshots, filters lists, limits audit and keeps source details', async () => {
    const review = { id: 7, normalized_question: '退款', ai_suggested_answer: null, occurrence_count: 2,
      review_status: '待审', approved_answer: null, created_at: 'date', updated_at: 'date' };
    const snapshots: Record<string, unknown> = {
      '/snapshots/review-queue.json': [review, { ...review, id: 8, review_status: '通过' }],
      '/snapshots/review-queue/7.json': { ...review, sources: [{ id: 1, raw_question: '我想退款', source: 'retrieval_low_conf', reason: '低分', created_at: 'date', retrieved_chunks: [] }] },
      '/snapshots/eval-runs.json': [{ id: 1, triggered_by: '手动', dataset_size: 300, metrics: { recall: 0.9 }, created_at: 'date' }],
      '/snapshots/strategy-comparison.json': { generated_from: { report: 'r', rankings: 'r', git_commit: 'hash' }, metrics: {}, cases: [] },
      '/snapshots/faith-cases.json': [{ id: 8, status: '未解决' }, { id: 9, status: '已解决' }],
      '/snapshots/tool-audit.json': [{ id: 1, status: '成功' }, { id: 2, status: '超时' }, { id: 3, status: '超时' }],
      '/snapshots/conversations.json': [{ session_id: '42', preview: '退款', summarized: true, created_at: 'date', updated_at: 'date' }],
      '/snapshots/conversations/42/messages.json': [{ id: 9, role: 'assistant', content: '您好', created_at: 'date' }],
      '/snapshots/knowledge/chunks/3.json': { id: 3, section_path: null, content_type: null, questions: '退款', answer: '七天', prev_chunk_id: null, next_chunk_id: null },
    };
    vi.stubGlobal('fetch', async (url: string) => {
      if (!(url in snapshots)) throw new Error(`unexpected fetch: ${url}`);
      return Response.json(snapshots[url]);
    });
    const source = new ReplayDataSource();
    expect(source.mode).toBe('replay');
    expect(await source.review.list('待审')).toEqual([review]);
    expect(await source.review.detail(7)).toMatchObject({ id: 7, sources: [{ raw_question: '我想退款' }] });
    expect(await source.evalRuns()).toEqual(snapshots['/snapshots/eval-runs.json']);
    expect(await source.strategyComparison()).toEqual(snapshots['/snapshots/strategy-comparison.json']);
    expect(await source.faithCases('未解决')).toEqual([{ id: 8, status: '未解决' }]);
    expect(await source.toolAudit(1, '超时')).toEqual([{ id: 2, status: '超时' }]);
    expect(await source.conversations('demo')).toEqual(snapshots['/snapshots/conversations.json']);
    expect(await source.messages(42, 'demo')).toEqual(snapshots['/snapshots/conversations/42/messages.json']);
    expect(await source.knowledgeChunk(3)).toEqual(snapshots['/snapshots/knowledge/chunks/3.json']);
  });
  it('blocks every write operation without fetching', async () => {
    vi.stubGlobal('fetch', () => { throw new Error('must not fetch'); });
    const source = new ReplayDataSource();
    const calls = [
      () => collectAsync(source.chat({ user_id: 'demo', message: '退款' })),
      () => collectAsync(source.resume({ user_id: 'demo', session_id: '42', order_id: '1001' })),
      () => source.feedback({ user_id: 'demo', conversation_id: 42, message_id: 9, rating: 'up' }),
      () => source.submitRefund({ user_id: 'demo', session_id: '42', order_id: '1001', reason: '七天无理由' }),
      () => source.submitTicket({ user_id: 'demo', session_id: '42', description: '损坏', ticket_type: '售后' }),
      () => source.review.approve(7, { approved_answer: '可退' }),
      () => source.review.reject(7),
      () => source.resolveFaithCase(8, { status: '已解决', resolution: '修正' }),
    ];
    for (const call of calls) await expect(call()).rejects.toBeInstanceOf(ReplayReadOnlyError);
  });
});
