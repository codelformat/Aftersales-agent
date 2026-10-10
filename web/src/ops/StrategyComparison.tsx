import { useCallback } from 'react';
import { ApiError } from '../data/DataSource';
import type { DataSource, Strategy } from '../data/DataSource';
import { dataSource } from '../data';
import { metrics } from './metrics';
import { LoadNotice, useResource } from './shared';
import styles from './Ops.module.css';

const strategies: Strategy[] = ['dense', 'bm25', 'hybrid', 'hybrid_rerank'];
export default function StrategyComparison({ source = dataSource }: { source?: DataSource }) {
  const loader = useCallback(async () => {
    try { return await source.strategyComparison(); }
    catch (error) {
      if (error instanceof ApiError && error.status === 404) throw new Error('暂无四策略对比报告');
      throw error;
    }
  }, [source]);
  const resource = useResource(loader);
  const data = resource.data;
  return <section aria-label="四策略对比">
    <h2>四策略对比</h2><p>误拒率越低越好，其余指标越高越好。每列最优值加粗，包含并列最优。</p>
    <LoadNotice {...resource} empty={false} emptyText="" />
    {resource.error && <button onClick={() => void resource.reload()}>重新加载策略对比</button>}
    {data && <>
      <div className={styles.tableWrap}><table className={styles.table} aria-label="四策略指标对比">
        <thead><tr><th scope="col">策略</th>{metrics.map(metric => <th key={metric.key} scope="col">{metric.label}</th>)}</tr></thead>
        <tbody>{strategies.map(strategy => <tr key={strategy}>
          <th scope="row">{strategy}</th>
          {metrics.map(metric => {
            const values = strategies.map(name => data.metrics[name][metric.comparisonKey]).filter(Number.isFinite);
            const best = metric.lowerIsBetter ? Math.min(...values) : Math.max(...values);
            const value = data.metrics[strategy][metric.comparisonKey];
            return <td key={metric.key}>{Number.isFinite(value) ? value === best ? <strong>{value.toFixed(3)}</strong> : value.toFixed(3) : '—'}</td>;
          })}
        </tr>)}</tbody>
      </table></div>
      <p className={styles.prose}>报告：{data.generated_from.report} · 排序：{data.generated_from.rankings} · commit：{data.generated_from.git_commit}</p>
      {data.generated_from.consistency_note && <p>{data.generated_from.consistency_note}</p>}
      <h2>代表题 Top-5</h2>
      {data.cases.slice(0, 3).map(item => <section key={item.id} aria-label={`${item.id} · ${item.query}`}>
        <h3>{item.id} · {item.query}</h3><p>{item.bucket}{item.fallback ? ' · 备用代表题' : ''}</p>
        <div className={styles.tableWrap}><div className={styles.rankings}>
          {strategies.map(strategy => <article key={strategy} className={styles.card}>
            <h3>{strategy}</h3>
            <ol aria-label={`${item.id} ${strategy} Top-5`}>{item.rankings[strategy].slice(0, 5).map((chunk, index) => <li key={index} className={chunk.relevant ? styles.relevant : undefined}>
              {chunk.key} {chunk.relevant && <span className={styles.badge}>相关</span>}
            </li>)}</ol>
            {item.rankings[strategy].length < 5 && <p>仅召回 {item.rankings[strategy].length} 项</p>}
          </article>)}
        </div></div>
      </section>)}
      {!data.cases.length && <p>暂无代表题</p>}
    </>}
  </section>;
}
