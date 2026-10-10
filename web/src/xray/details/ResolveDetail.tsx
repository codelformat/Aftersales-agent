import type { TraceEvent } from '../../protocol/events';
import { textDiff } from '../textDiff';
import styles from '../Xray.module.css';
export default function ResolveDetail({ data }: { data: Extract<TraceEvent, { kind: 'resolve' }>['data'] }) {
  const flags = [[data.order_scoped, '订单范围'], [data.ticket_request, '建工单'], [data.status_query, '状态查询'], [data.history_recall, '回顾历史']] as const;
  return <section className={styles.detail} aria-label="指代消解详情">
    <h3>原句</h3><p>{data.original}</p>
    <h3>改写句</h3><p>{textDiff(data.original, data.resolved_input).map((part, i) => part.added ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>)}</p>
    <h3>标准查询</h3><p>{data.standard_query}</p>
    <div className={styles.chips}>{data.order_id && <span className={styles.chip}>订单 #{data.order_id}</span>}
      {flags.filter(([enabled]) => enabled).map(([, label]) => <span className={styles.chip} key={label}>{label}</span>)}</div>
  </section>;
}
