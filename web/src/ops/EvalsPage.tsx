import { useCallback } from 'react';
import type { DataSource } from '../data/DataSource';
import { dataSource } from '../data';
import LineChart from './charts/LineChart';
import { metrics, metricValue } from './metrics';
import StrategyComparison from './StrategyComparison';
import { formatDate, LoadNotice, useListResource } from './shared';
import styles from './Ops.module.css';

export default function EvalsPage({ source = dataSource }: { source?: DataSource }) {
  const loader = useCallback(() => source.evalRuns(), [source]);
  const resource = useListResource(loader, '评估轮次格式错误');
  const ordered = [...(resource.data ?? [])].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime() || a.id - b.id);
  const latest = ordered.at(-1);
  const points = ordered.filter(run => run.dataset_size === latest?.dataset_size);
  const previous = points.at(-2);
  const decliningKeys = new Set(metrics.filter(metric => {
    const current = metricValue(latest, metric.key), before = metricValue(previous, metric.key);
    if (current === null || before === null) return false;
    return (metric.lowerIsBetter ? current - before : before - current) > .02 + 1e-10;
  }).map(metric => metric.key));
  return <section className={styles.page}>
    <h1>评估趋势</h1><p>仅比较与最新一轮题数相同的轮次。误拒率越低越好，其余指标越高越好。</p>
    <div className={styles.toolbar}>
      <button disabled={resource.loading} onClick={() => void resource.reload()}>刷新</button>
      {latest && <span role="status">数据集 {latest.dataset_size} 题 · {points.length} 轮（最近 {ordered.length} 轮中） · 最新 {formatDate(latest.created_at)}</span>}
    </div>
    <LoadNotice {...resource} empty={resource.data?.length === 0} emptyText="暂无评估轮次" />
    {latest && <>
      <p role="status" className={decliningKeys.size ? styles.error : undefined}>下滑指标：{decliningKeys.size ? metrics.filter(metric => decliningKeys.has(metric.key)).map(metric => metric.label).join('、') : '无'}</p>
      {points.length === 1 && <p role="status">至少需要两轮才能看趋势</p>}
      <div className={styles.charts}>{metrics.map(metric => {
        const current = metricValue(latest, metric.key), before = metricValue(previous, metric.key);
        const declining = decliningKeys.has(metric.key);
        return <section key={metric.key} className={styles.metric} data-declining={declining}>
          <div className={styles.metricHeader}><h2>{metric.label}</h2><strong>{current?.toFixed(3) ?? '—'}</strong></div>
          <p>{current !== null && before !== null ? `较上一轮 ${current - before > 0 ? '+' : ''}${(current - before).toFixed(3)}${declining ? ' · 下滑' : ''}` : previous ? '缺少指标，无法比较上一轮' : '暂无上一轮'}</p>
          <LineChart label={metric.label} declining={declining} points={points.map(point => ({ id: point.id, created_at: point.created_at, value: metricValue(point, metric.key) }))} />
          {!points.some(point => metricValue(point, metric.key) !== null) && <p>暂无该指标数据</p>}
        </section>;
      })}</div>
    </>}
    <StrategyComparison source={source} />
  </section>;
}
