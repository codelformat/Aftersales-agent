import type { TraceEvent } from '../../protocol/events';
import styles from '../Xray.module.css';
export default function ContextDetail({ data }: { data: Extract<TraceEvent, { kind: 'context' }>['data'] }) {
  return <section className={styles.detail} aria-label="上下文详情">
    {[[data.layer1_tokens, data.layer1_budget], [data.layer2_tokens, data.layer2_budget]].map(([tokens, budget], i) => <div key={i}>
      <p>层 {i + 1}：{tokens} / {budget} token</p>
      <meter aria-label={`层 ${i + 1} token`} min={0} max={Math.max(1, budget)} value={tokens} />
      {tokens > budget && <span className={styles.status} data-status="interrupted">超出预算</span>}
    </div>)}
    <p>{data.summary_triggered ? '已触发摘要' : '未触发摘要'}</p>
  </section>;
}
