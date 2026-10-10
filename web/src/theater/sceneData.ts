import type { Scene, StrategyComparison } from '../data/DataSource';
export function strategyScene(snapshot: StrategyComparison): Scene {
  // These chapters are derived from snapshot records; they are not recorded SSE.
  const lines: Scene['lines'] = [
    { t_ms: 0, lane: 'ops', channel: 'api', event: 'strategy_source', data: snapshot.generated_from },
    ...snapshot.cases.slice(0, 3).map((data, index) => ({
      t_ms: (index + 1) * 3000, lane: 'ops' as const, channel: 'api' as const, event: 'strategy_case', data,
    })),
    { t_ms: 12000, lane: 'ops', channel: 'api', event: 'strategy_metrics', data: snapshot.metrics },
  ];
  const date = /rag_eval_(\d{4})(\d{2})(\d{2})/.exec(snapshot.generated_from.report);
  return {
    header: { scene: 'strategies', title_zh: '检索：四策略对比', title_en: 'Four retrieval strategies',
      recorded_at: date ? `${date[1]}-${date[2]}-${date[3]}` : '日期未记录',
      git_commit: snapshot.generated_from.git_commit, model: '模型未记录（评估快照）' },
    lines,
  };
}
