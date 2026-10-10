import type { EvalRun, StrategyMetrics } from '../data/DataSource';

export const metrics: { key: string; comparisonKey: keyof StrategyMetrics; label: string; lowerIsBetter?: boolean }[] = [
  { key: 'recall_at_1', comparisonKey: 'R@1', label: 'R@1' },
  { key: 'recall_at_3', comparisonKey: 'R@3', label: 'R@3' },
  { key: 'recall_at_5', comparisonKey: 'R@5', label: 'R@5' },
  { key: 'recall_at_10', comparisonKey: 'R@10', label: 'R@10' },
  { key: 'mrr', comparisonKey: 'MRR', label: 'MRR' },
  { key: 'faithfulness', comparisonKey: 'faithfulness', label: '忠实度' },
  { key: 'false_refusal', comparisonKey: 'false_refusal', label: '误拒率', lowerIsBetter: true },
  { key: 'd_refusal', comparisonKey: 'd_refusal', label: 'D 拒答率' },
];
export function metricValue(run: EvalRun | undefined, key: string): number | null {
  const value = run?.metrics[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
