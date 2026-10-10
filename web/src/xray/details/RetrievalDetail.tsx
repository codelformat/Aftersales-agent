import type { TraceEvent } from '../../protocol/events';
import styles from '../Xray.module.css';
// 与 app/config.py 的 RERANK_MIN_SCORE 一致；trace 不携带此常量。
const RERANK_MIN_SCORE = 0.20;
export default function RetrievalDetail({ data, highlightedChunkId }: {
  data: Extract<TraceEvent, { kind: 'retrieval' }>['data']; highlightedChunkId?: number | null;
}) {
  const visible = data.top.slice(0, 5);
  const hovered = data.top.find(chunk => chunk.chunk_id === highlightedChunkId);
  if (hovered && !visible.some(chunk => chunk.chunk_id === hovered.chunk_id)) visible.push(hovered);
  return <section className={styles.detail} aria-label="检索详情">
    <div className={styles.chips}>{data.queries.map((query, i) => <span className={styles.chip} key={i}>{query}</span>)}</div>
    <p>保留 {data.kept} 条 · 重排门槛 {RERANK_MIN_SCORE.toFixed(2)}</p>
    <ol className={styles.retrieval}>{visible.map((chunk, i) => <li key={`${chunk.chunk_id}-${i}`} aria-label={`${chunk.section_path} · chunk #${chunk.chunk_id}`}
      data-highlighted={highlightedChunkId === chunk.chunk_id} data-below-threshold={chunk.score < RERANK_MIN_SCORE}>
      <div>{chunk.section_path} <span>#{chunk.chunk_id}</span></div>
      <meter aria-label={`${chunk.section_path} 分数`} min={0} max={1} value={chunk.score} /> <span>{chunk.score.toFixed(3)}</span>
      {chunk.score < RERANK_MIN_SCORE && <span className={styles.chip}>低于门槛</span>}
      <p>{chunk.question}</p>
    </li>)}</ol>
  </section>;
}
