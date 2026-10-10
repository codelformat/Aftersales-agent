import type { TraceEvent } from '../../protocol/events';
import styles from '../Xray.module.css';
export default function GateDetail({ data }: { data: Extract<TraceEvent, { kind: 'gate' }>['data'] }) {
  const signals = [data.signals.top1, data.signals.effective, data.signals.margin];
  const names = ['Top1', '有效证据', '分差'];
  const widths = signals.map((signal, i) => data.weights[i] * signal * 100);
  let x = 0;
  return <section className={styles.detail} aria-label="置信度闸详情">
    <p className={styles.status} data-status={data.passed ? 'completed' : 'interrupted'}>{data.passed ? '通过' : '拦下'}</p>
    <svg viewBox="0 0 100 16" role="img" aria-label={`置信度 ${data.confidence.toFixed(3)}，门槛 ${data.threshold.toFixed(3)}`} className={styles.gate}>
      <rect x="0" y="4" width="100" height="8" className={styles.track} />
      {widths.map((width, i) => {
        const position = x; x += width;
        return <rect key={names[i]} data-signal={names[i]} x={position} y="4" width={width} height="8" className={styles[`segment${i}`]} />;
      })}
      <line data-threshold={data.threshold} x1={data.threshold * 100} x2={data.threshold * 100} y1="1" y2="15" className={styles.threshold} />
    </svg>
    <p>置信度 {data.confidence.toFixed(3)} · 门槛 {data.threshold.toFixed(3)}</p>
    {signals.map((signal, i) => <p key={names[i]}>{names[i]}：{data.weights[i].toFixed(3)} × {signal.toFixed(3)} = {(widths[i] / 100).toFixed(3)}</p>)}
    <p>{data.self_check ? (data.source === 'self_check' ? '自评拦下' : '自评通过') : '未调用自评'}</p>
    {data.source && <span className={styles.chip}>{data.source}</span>}
    {data.reason && <p>{data.reason}</p>}
  </section>;
}
