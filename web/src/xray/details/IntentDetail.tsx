import type { TraceEvent } from '../../protocol/events';
import styles from '../Xray.module.css';
export default function IntentDetail({ data }: { data: Extract<TraceEvent, { kind: 'intent' }>['data'] }) {
  return <section className={styles.detail} aria-label="意图详情">
    <p>{data.intent ?? '未识别意图'}</p>
    {data.confidence === null ? <p>置信度未提供</p> : <>
      <svg viewBox="0 0 100 16" role="img" aria-label={`意图置信度 ${data.confidence.toFixed(3)}，升级门槛 0.700`} className={styles.gate}>
        <rect width="100" height="8" y="4" className={styles.track} />
        <rect width={Math.max(0, Math.min(1, data.confidence)) * 100} height="8" y="4" className={styles.segment0} />
        <line x1="70" x2="70" y1="1" y2="15" className={styles.threshold} />
      </svg>
      <p>置信度 {data.confidence.toFixed(3)} · 升级门槛 0.700</p>
    </>}
    <div className={styles.chips}><span className={styles.chip}>路由 {data.route}</span>
      {data.escalated && <span className={styles.chip}>已升级</span>}</div>
  </section>;
}
