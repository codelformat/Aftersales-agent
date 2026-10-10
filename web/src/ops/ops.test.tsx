import type { ComponentType } from 'react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LiveDataSource } from '../data/live';
import { ReplayDataSource } from '../data/replay';
import type { DataSource, EvalRun, FaithCase, ReviewDetail, StrategyComparison, ToolAuditRow } from '../data/DataSource';
import { RoutePage } from '../app/router';
import { deferred } from '../desk/test-utils';

// A missing component is an explicit RED assertion, not a module resolution error.
const modules = import.meta.glob<{ default: ComponentType<{ source?: DataSource }> }>('./*Page.tsx');
async function mount(name: string, source: DataSource = new LiveDataSource()) {
  const load = modules[`./${name}.tsx`];
  expect(load, `${name} must implement the migrated interactions`).toBeTypeOf('function');
  const { default: Page } = await load!();
  return render(<Page source={source} />);
}
const date = '2026-10-09T10:00:00Z';
const review: ReviewDetail = {
  id: 1, normalized_question: '退款多久？', ai_suggested_answer: ' （待核实） 三个工作日', occurrence_count: 3,
  review_status: '待审', approved_answer: null, created_at: date, updated_at: date,
  sources: [{ id: 4, raw_question: '钱啥时候到？', source: 'user_feedback', reason: '答复不准确', created_at: date,
    retrieved_chunks: [{ section_path: '退款政策', score: 0.85, questions: '多久退款', answer: '三个工作日' }] }],
};
const faith: FaithCase = {
  id: 2, eval_id: 'A01', bucket: 'A_policy', query: '保修多久？', strategy: 'dense', answer: '终身保修[1]',
  reason: '证据只有一年', citations: [{ n: 1, section_path: '保修', question: '期限', answer: '一年' },
    { n: 2, section_path: '退货', question: '天数', answer: '七天' }], cited: [1], judge_model: 'judge',
  status: '未解决', seen_count: 2, first_seen_at: date, last_seen_at: date, resolution: '旧处置', resolved_at: date,
};
const audit: ToolAuditRow = {
  id: 3, created_at: date, conversation_id: 1, tool_call_id: 'call-1', tool_name: 'query_logistics',
  tool_source: 'mcp', mcp_server: 'logistics', status: '成功', retry_count: 1, duration_ms: 125,
  error_message: '查询落空', result_summary: null,
};
const strategies = ['dense', 'bm25', 'hybrid', 'hybrid_rerank'] as const;
const comparison: StrategyComparison = {
  generated_from: { report: 'report.md', rankings: 'rankings.json', git_commit: 'abc', consistency_note: '两次运行独立' },
  metrics: {
    dense: { 'R@1': .7, 'R@3': .8, 'R@5': .8, 'R@10': .9, MRR: .8, faithfulness: .9, false_refusal: .04, d_refusal: .9 },
    bm25: { 'R@1': .6, 'R@3': .7, 'R@5': .8, 'R@10': .9, MRR: .7, faithfulness: .8, false_refusal: .01, d_refusal: .8 },
    hybrid: { 'R@1': .8, 'R@3': .9, 'R@5': .9, 'R@10': 1, MRR: .9, faithfulness: .9, false_refusal: .03, d_refusal: .9 },
    hybrid_rerank: { 'R@1': .9, 'R@3': .9, 'R@5': 1, 'R@10': 1, MRR: 1, faithfulness: 1, false_refusal: .02, d_refusal: 1 },
  },
  cases: ['C01', 'B12', 'A02'].map(id => ({ id, query: `代表题 ${id}`, bucket: 'A', relevant: [['正确证据']],
    rankings: Object.fromEntries(strategies.map(strategy => [strategy, Array.from({ length: 6 }, (_, i) =>
      ({ key: i === 0 ? '正确证据' : `候选 ${i}`, relevant: i === 0 }))])) as StrategyComparison['cases'][number]['rankings'] })),
};
function run(id: number, size: number, metrics: Record<string, unknown>): EvalRun {
  return { id, dataset_size: size, triggered_by: '手动', created_at: `2026-10-09T${String(id).padStart(2, '0')}:00:00Z`, metrics };
}
let reviews: ReviewDetail[];
let cases: FaithCase[];
let runs: EvalRun[];
let audits: ToolAuditRow[];
let requests: { url: URL; init?: RequestInit }[];
let approveError: number | undefined;
let detailFails: boolean;
let strategyFails: boolean;
let listFails: boolean;
let malformedPaths: Set<string>;
let resolveError = false;
let rejectConflict = false;
let holdReject: ReturnType<typeof deferred<void>> | undefined;
let holdReview: ReturnType<typeof deferred<Response>> | undefined;
let holdApprove: ReturnType<typeof deferred<Response>> | undefined;
function json(data: unknown, status = 200) { return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }); }
beforeEach(() => {
  malformedPaths = new Set(); resolveError = false; rejectConflict = false; holdReview = undefined; holdReject = undefined;
  reviews = [structuredClone(review)]; cases = [structuredClone(faith)]; runs = []; audits = [audit]; requests = [];
  approveError = undefined; detailFails = false; strategyFails = false; listFails = false; holdApprove = undefined;
  vi.stubGlobal('fetch', async (input: string, init?: RequestInit) => {
    const url = new URL(input, 'http://localhost'); requests.push({ url, init });
    const path = url.pathname.replace('/snapshots/', '/api/').replace(/\.json$/, '');
    const status = url.searchParams.get('status');
    if (malformedPaths.has(path)) return json({ unexpected: 'not a list' });
    if (path === '/api/review-queue' && status === '待审' && holdReview) return holdReview.promise;
    if (path === '/api/review-queue') return listFails ? json({}, 500) : json(reviews.filter(r => !status || r.review_status === status));
    if (path === '/api/review-queue/1') return detailFails ? json({}, 500) : json(reviews[0]);
    if (path.endsWith('/approve')) {
      if (holdApprove) return holdApprove.promise;
      if (approveError) {
        if (approveError !== 422) reviews[0]!.review_status = '通过';
        return json({ detail: approveError === 422 ? [{ loc: ['body', 'approved_answer'], msg: 'Invalid value' }] :
          { message: approveError === 409 ? '该问题已审核' : '已通过，向量化失败，请运行 build_kb.py 补齐' } }, approveError);
      }
      reviews[0]!.review_status = '通过'; return json({ chunk_id: 9, vectorized: 1 });
    }
    if (path.endsWith('/reject') && holdReject) await holdReject.promise;
    if (path.endsWith('/reject') && rejectConflict) { reviews[0]!.review_status = '通过'; return json({ detail: { message: '该问题已审核' } }, 409); }
    if (path.endsWith('/reject')) { reviews[0]!.review_status = '驳回'; return json({ id: 1, review_status: '驳回' }); }
    if (path === '/api/eval-runs') return json(runs);
    if (path === '/api/strategy-comparison') return strategyFails ? json({ code: 'not_exported' }, 404) : json(comparison);
    if (path === '/api/faith-cases') return json(cases.filter(c => !status || c.status === status));
    if (path.endsWith('/resolve') && resolveError) return json({ detail: { message: '个案不存在' } }, 404);
    if (path.endsWith('/resolve')) { cases[0] = { ...cases[0]!, ...JSON.parse(String(init?.body)), resolved_at: date }; return json(cases[0]); }
    if (path === '/api/tool-audit') return json(audits.filter(a => !status || a.status === status));
    throw new Error(`Unexpected request ${input}`);
  });
});
afterEach(() => vi.unstubAllGlobals());

it('ops routes render the sub-navigation and actual review list', async () => {
  render(<RoutePage route={{ section: 'ops', title: '待审队列', showViewMode: false }} />);
  expect(screen.getByRole('navigation', { name: '运营台子导航' })).toBeInTheDocument();
  expect(await screen.findByRole('button', { name: '退款多久？' })).toBeInTheDocument();
  for (const [name, path] of [['待审队列', 'review'], ['评估', 'evals'], ['编造台账', 'faith'], ['工具审计', 'tools']]) {
    expect(within(screen.getByRole('navigation', { name: '运营台子导航' })).getByRole('link', { name })).toHaveAttribute('href', `#/ops/${path}`);
  }
});
it('review approval defaults remove the pending prefix and use 通用, submits trimmed answer', async () => {
  const user = userEvent.setup(); await mount('ReviewPage');
  await user.click(await within(screen.getByRole('table', { name: '待审问题列表' })).findByRole('button', { name: '通过' }));
  expect(screen.getByRole('textbox', { name: '核准答案' })).toHaveValue('三个工作日');
  expect(screen.getByRole('combobox', { name: '品类' })).toHaveValue('通用');
  await user.clear(screen.getByRole('textbox', { name: '核准答案' }));
  await user.type(screen.getByRole('textbox', { name: '核准答案' }), ' 新答案 ');
  await user.selectOptions(screen.getByRole('combobox', { name: '品类' }), '台灯');
  await user.click(screen.getByRole('button', { name: '确认通过' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(JSON.parse(String(requests.find(r => r.url.pathname.endsWith('/approve'))?.init?.body)))
    .toEqual({ approved_answer: '新答案', product_category: '台灯' });
  expect(screen.getByText(/已通过并入库/)).toBeInTheDocument();
});
it('review rejects blank approval without sending a write', async () => {
  const user = userEvent.setup(); await mount('ReviewPage'); await user.click(await within(screen.getByRole('table', { name: '待审问题列表' })).findByRole('button', { name: '通过' }));
  await user.clear(screen.getByRole('textbox', { name: '核准答案' })); await user.click(screen.getByRole('button', { name: '确认通过' }));
  expect(screen.getByRole('alert')).toHaveTextContent('请填写核准答案');
  expect(requests.filter(r => r.init?.method === 'POST')).toHaveLength(0);
});
it.each([409, 422, 502])('review handles HTTP %i without losing the form or re-submitting committed approval', async status => {
  approveError = status;
  const user = userEvent.setup(); await mount('ReviewPage'); await user.click(await within(screen.getByRole('table', { name: '待审问题列表' })).findByRole('button', { name: '通过' }));
  await user.click(screen.getByRole('button', { name: '确认通过' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(status === 409 ? '该问题已审核' : status === 422 ? '输入不合法' : '已通过，向量化失败，请运行 build_kb.py 补齐');
  if (status === 422) expect(screen.getByRole('button', { name: '确认通过' })).toBeEnabled();
  else {
    await waitFor(() => expect(screen.getByRole('button', { name: '确认通过' })).toBeDisabled());
    expect(requests.filter(r => r.url.pathname === '/api/review-queue')).toHaveLength(2);
  }
});
it('review prevents duplicate submit and Escape dismissal while saving', async () => {
  holdApprove = deferred<Response>(); const user = userEvent.setup(); await mount('ReviewPage');
  await user.click(await within(screen.getByRole('table', { name: '待审问题列表' })).findByRole('button', { name: '通过' })); await user.click(screen.getByRole('button', { name: '确认通过' }));
  expect(screen.getByRole('button', { name: '提交中…' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '取消' })).toBeDisabled(); await user.keyboard('{Escape}');
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  await act(async () => holdApprove!.resolve(json({ chunk_id: 9, vectorized: 1 })));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});
it('review expands raw question and retrieval snapshot, retries detail failure by reopening', async () => {
  const user = userEvent.setup(); detailFails = true; await mount('ReviewPage');
  const expand = await screen.findByRole('button', { name: '退款多久？' }); await user.click(expand);
  expect(await screen.findByRole('alert')).toHaveTextContent('收起后展开可重试');
  await user.click(expand); detailFails = false; await user.click(expand);
  expect(await screen.findByText('钱啥时候到？')).toBeInTheDocument(); expect(screen.getByText('用户反馈')).toBeInTheDocument();
  expect(screen.getByText(/退款政策.*0.85/)).toBeInTheDocument();
});
it('review filters and rejects, then refreshes the queue', async () => {
  const user = userEvent.setup(); await mount('ReviewPage'); await user.click(await within(screen.getByRole('table', { name: '待审问题列表' })).findByRole('button', { name: '驳回' }));
  expect(await screen.findByText(/已驳回/)).toBeInTheDocument();
  await user.click(within(screen.getByRole('group', { name: '按状态筛选' })).getByRole('button', { name: '驳回' }));
  expect(await screen.findByRole('button', { name: '退款多久？' })).toBeInTheDocument();
  expect(requests.at(-1)?.url.searchParams.get('status')).toBe('驳回');
});
it('review list failure supports refresh', async () => {
  listFails = true; await mount('ReviewPage'); expect(await screen.findByRole('alert')).toBeInTheDocument();
  listFails = false; await userEvent.click(screen.getByRole('button', { name: '刷新' }));
  expect(await screen.findByRole('button', { name: '退款多久？' })).toBeInTheDocument();
});
it.each(['ReviewPage', 'FaithPage'])('%s disables replay writes with the local-run explanation but keeps reads', async name => {
  await mount(name, new ReplayDataSource());
  const labels = name === 'ReviewPage' ? ['通过', '驳回'] : ['将题号 A01 标记为已解决', '将题号 A01 标记为无需解决'];
  for (const label of labels) expect(await within(screen.getByRole('table', { name: name === 'ReviewPage' ? '待审问题列表' : '编造个案列表' })).findByRole('button', { name: label })).toBeDisabled();
  expect(screen.getByText('本地运行可操作')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: name === 'ReviewPage' ? '退款多久？' : '保修多久？' }));
  expect(requests.every(r => r.url.pathname.startsWith('/snapshots/'))).toBe(true);
});
it('evals use only latest dataset size and chronological timestamps, flag worsening > .02', async () => {
  runs = [run(4, 300, { recall_at_1: .7, false_refusal: .15, mrr: .78 }), run(3, 20, { recall_at_1: 1 }),
    run(2, 300, { recall_at_1: .8, false_refusal: .1, mrr: .8 })];
  await mount('EvalsPage');
  const chart = await screen.findByRole('img', { name: 'R@1随时间变化，最新一轮下滑' });
  expect(chart.querySelectorAll('circle')).toHaveLength(2);
  expect(chart.textContent).toContain('第 2 轮'); expect(chart.textContent).not.toContain('第 3 轮');
  expect(chart.closest('section')).toHaveAttribute('data-declining', 'true');
  expect(screen.getByRole('img', { name: '误拒率随时间变化，最新一轮下滑' }).closest('section')).toHaveAttribute('data-declining', 'true');
  expect(screen.getByRole('img', { name: 'MRR随时间变化' }).closest('section')).toHaveAttribute('data-declining', 'false');
  expect(screen.getAllByRole('img')).toHaveLength(8);
});
it('eval charts break at missing values rather than plotting zero; equal timestamps stay finite', async () => {
  runs = [run(1, 300, { recall_at_1: .6 }), run(2, 300, {}), { ...run(3, 300, { recall_at_1: .7 }), created_at: run(1, 300, {}).created_at }];
  runs[1]!.created_at = runs[0]!.created_at;
  await mount('EvalsPage'); const chart = await screen.findByRole('img', { name: 'R@1随时间变化' });
  expect(chart.querySelector('path')?.getAttribute('d')?.match(/M/g)).toHaveLength(2);
  expect(chart.innerHTML).not.toMatch(/NaN|Infinity/); expect(chart.querySelectorAll('circle')).toHaveLength(2);
});
it('evals show empty and single-round notices and refresh', async () => {
  await mount('EvalsPage'); expect(await screen.findByText('暂无评估轮次')).toBeInTheDocument();
  runs = [run(1, 300, {})]; await userEvent.click(screen.getByRole('button', { name: '刷新' }));
  expect(await screen.findByText('至少需要两轮才能看趋势')).toBeInTheDocument();
});
it('strategy table emphasizes per-column best including minimum refusal and tied maxima', async () => {
  await mount('EvalsPage'); const table = await screen.findByRole('table', { name: '四策略指标对比' });
  const bm25 = within(table).getByRole('row', { name: /bm25/ });
  expect(within(bm25).getAllByRole('cell')[6]!.querySelector('strong')).toHaveTextContent('0.010');
  const hybrid = within(table).getByRole('row', { name: /^hybrid / });
  expect(within(hybrid).getAllByRole('cell')[1]!.querySelector('strong')).toHaveTextContent('0.900');
  expect(within(hybrid).getAllByRole('cell')[0]!.querySelector('strong')).toBeNull();
  expect(within(table).getAllByRole('columnheader')).toHaveLength(9);
});
it('strategy comparison shows three questions and four Top-5 lists with relevant marks and provenance', async () => {
  await mount('EvalsPage'); await screen.findByRole('table', { name: '四策略指标对比' });
  for (const id of ['C01', 'B12', 'A02']) {
    const region = screen.getByRole('region', { name: `${id} · 代表题 ${id}` });
    expect(within(region).getAllByRole('list')).toHaveLength(4);
    for (const list of within(region).getAllByRole('list')) {
      expect(within(list).getAllByRole('listitem')).toHaveLength(5);
      expect(within(list).getByText('相关')).toBeInTheDocument();
    }
    expect(within(region).queryByText('候选 5')).not.toBeInTheDocument();
  }
  expect(screen.getByText(/report.md/)).toBeInTheDocument(); expect(screen.getByText('两次运行独立')).toBeInTheDocument();
});
it('missing strategy export does not hide valid evaluation trends and can retry', async () => {
  strategyFails = true; runs = [run(1, 300, { recall_at_1: .8 })]; await mount('EvalsPage');
  expect(await screen.findByText('暂无四策略对比报告')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'R@1随时间变化' })).toBeInTheDocument();
  strategyFails = false; await userEvent.click(screen.getByRole('button', { name: '重新加载策略对比' }));
  expect(await screen.findByRole('table', { name: '四策略指标对比' })).toBeInTheDocument();
});
it('faith details include cited and uncited evidence, recurrence and resolution history', async () => {
  const user = userEvent.setup(); await mount('FaithPage'); await user.click(await screen.findByRole('button', { name: '保修多久？' }));
  expect(screen.getByText('终身保修[1]')).toBeInTheDocument(); expect(screen.getByText('证据只有一年')).toBeInTheDocument();
  expect(screen.getAllByText('已引用')).toHaveLength(1); expect(screen.getByText('[2] 退货')).toBeInTheDocument();
  expect(screen.getByText('复发')).toBeInTheDocument(); expect(screen.getByText('旧处置')).toBeInTheDocument();
});
it.each(['已解决', '无需解决'])('faith resolves %s with trimmed explanation, then updates current filter', async status => {
  const user = userEvent.setup(); await mount('FaithPage');
  await user.click(within(screen.getByRole('group', { name: '按状态筛选' })).getByRole('button', { name: '未解决' }));
  await user.click(await screen.findByRole('button', { name: `将题号 A01 标记为${status}` }));
  await user.type(screen.getByRole('textbox', { name: '处置说明' }), ' 修正知识库 ');
  await user.click(screen.getByRole('button', { name: '确认提交' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.queryByRole('button', { name: '保修多久？' })).not.toBeInTheDocument();
  expect(JSON.parse(String(requests.find(r => r.url.pathname.endsWith('/resolve'))?.init?.body))).toEqual({ status, resolution: '修正知识库' });
});
it('faith validates 1–300 Unicode characters and allows cancellation', async () => {
  const user = userEvent.setup(); await mount('FaithPage'); await user.click(await screen.findByRole('button', { name: '将题号 A01 标记为已解决' }));
  await user.click(screen.getByRole('button', { name: '确认提交' })); expect(screen.getByRole('alert')).toHaveTextContent('请填写 1–300 字的处置说明');
  await user.type(screen.getByRole('textbox', { name: '处置说明' }), '😀'.repeat(301));
  expect(screen.getByText('301 / 300')).toBeInTheDocument(); await user.click(screen.getByRole('button', { name: '确认提交' }));
  expect(requests.filter(r => r.init?.method === 'POST')).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: '取消' })); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('tool audit filters status at DataSource boundary and displays timing, source, retry and errors', async () => {
  audits.push({ ...audit, id: 4, tool_name: 'create_ticket', status: '权限拒绝', duration_ms: null, error_message: '写操作未确认' });
  const user = userEvent.setup(); await mount('ToolAuditPage'); expect(await screen.findByText('query_logistics')).toBeInTheDocument();
  expect(screen.getByText('125 ms')).toBeInTheDocument(); expect(within(screen.getByText('query_logistics').closest('tr')!).getByText('mcp · logistics')).toBeInTheDocument();
  expect(screen.getByText('查询落空')).toBeInTheDocument();
  await user.selectOptions(screen.getByRole('combobox', { name: '状态筛选' }), '权限拒绝');
  await waitFor(() => expect(screen.queryByText('query_logistics')).not.toBeInTheDocument());
  expect(await screen.findByText('create_ticket')).toBeInTheDocument();
  expect(requests.at(-1)?.url.searchParams.get('status')).toBe('权限拒绝'); expect(requests.at(-1)?.url.searchParams.get('limit')).toBe('50');
  await user.selectOptions(screen.getByRole('combobox', { name: '状态筛选' }), '');
  expect(await screen.findByText('query_logistics')).toBeInTheDocument();
});

it.each([
  ['ReviewPage', '/api/review-queue', '问题列表格式错误'],
  ['FaithPage', '/api/faith-cases', '个案列表格式错误'],
  ['EvalsPage', '/api/eval-runs', '评估轮次格式错误'],
  ['ToolAuditPage', '/api/tool-audit', '工具审计列表格式错误'],
])('%s handles a malformed list with a recoverable notice', async (page, path, message) => {
  malformedPaths.add(path); await mount(page);
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  malformedPaths.clear(); await userEvent.click(screen.getByRole('button', { name: page === 'FaithPage' ? '重新加载' : '刷新' }));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});
it('slow review filter response cannot overwrite a newer selection', async () => {
  holdReview = deferred<Response>(); const user = userEvent.setup(); await mount('ReviewPage');
  await user.click(within(screen.getByRole('group', { name: '按状态筛选' })).getByRole('button', { name: '全部' }));
  expect(await screen.findByRole('button', { name: '退款多久？' })).toBeInTheDocument();
  await act(async () => holdReview!.resolve(json([])));
  expect(screen.getByRole('button', { name: '退款多久？' })).toBeInTheDocument();
  expect(within(screen.getByRole('group', { name: '按状态筛选' })).getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true');
});
it('review reject conflict refreshes current state and reports the server error', async () => {
  rejectConflict = true; await mount('ReviewPage');
  await userEvent.click(await within(screen.getByRole('table', { name: '待审问题列表' })).findByRole('button', { name: '驳回' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('该问题已审核');
  expect(screen.queryByRole('button', { name: '退款多久？' })).not.toBeInTheDocument();
});
it('faith failed resolution keeps the draft for retry and Escape restores the action focus', async () => {
  resolveError = true; const user = userEvent.setup(); await mount('FaithPage');
  const action = await screen.findByRole('button', { name: '将题号 A01 标记为已解决' }); await user.click(action);
  await user.type(screen.getByRole('textbox', { name: '处置说明' }), '修正证据');
  await user.click(screen.getByRole('button', { name: '确认提交' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('个案不存在');
  expect(screen.getByRole('textbox', { name: '处置说明' })).toHaveValue('修正证据');
  await user.keyboard('{Escape}'); await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(action).toHaveFocus();
});

it('rejection refresh uses the filter selected while its request was pending', async () => {
  reviews.push({ ...review, id: 5, normalized_question: '仍然待审的问题' });
  holdReject = deferred<void>(); const user = userEvent.setup(); await mount('ReviewPage');
  await user.click(await within(screen.getByRole('table', { name: '待审问题列表' })).findAllByRole('button', { name: '驳回' }).then(buttons => buttons[0]!));
  await user.click(within(screen.getByRole('group', { name: '按状态筛选' })).getByRole('button', { name: '驳回' }));
  expect(await screen.findByText('当前筛选下暂无问题')).toBeInTheDocument();
  await act(async () => holdReject!.resolve(undefined));
  expect(await screen.findByText(/已驳回/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '退款多久？' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '仍然待审的问题' })).not.toBeInTheDocument();
  expect(requests.at(-1)?.url.searchParams.get('status')).toBe('驳回');
});
it.each([409, 502])('closing approval after HTTP %i focuses a stable page heading', async status => {
  approveError = status; const user = userEvent.setup(); await mount('ReviewPage');
  await user.click(await within(screen.getByRole('table', { name: '待审问题列表' })).findByRole('button', { name: '通过' }));
  await user.click(screen.getByRole('button', { name: '确认通过' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认通过' })).toBeDisabled());
  await user.click(screen.getByRole('button', { name: '取消' }));
  await waitFor(() => expect(screen.getByRole('heading', { name: '待审队列', level: 1 })).toHaveFocus());
});
