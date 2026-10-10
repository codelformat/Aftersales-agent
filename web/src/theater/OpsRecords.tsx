import type { SceneLine } from '../data/DataSource';
import { object } from './projectRecording';
import styles from './Theater.module.css';
function RecordView({ value }: { value: unknown }) {
  const row = object(value);
  const sources = Array.isArray(row.sources) ? row.sources : [];
  return <article className={styles.record}>
    {typeof row.normalized_question === 'string' && <h3>{row.normalized_question}</h3>}
    {typeof row.review_status === 'string' && <span className={styles.badge}>{row.review_status}</span>}
    {typeof row.ai_suggested_answer === 'string' && <p>建议答案：{row.ai_suggested_answer}</p>}
    {typeof row.approved_answer === 'string' && <p>{row.approved_answer}</p>}
    {row.chunk_id !== undefined && <p>知识块 #{String(row.chunk_id)} · 向量化 {String(row.vectorized ?? '未记录')}</p>}
    {sources.map((source, index) => {
      const item = object(source);
      const chunks = Array.isArray(item.retrieved_chunks) ? item.retrieved_chunks : [];
      return <section key={index} aria-label="召回快照"><p>{String(item.raw_question ?? '')}</p><p>{String(item.reason ?? '')}</p>
        <ul>{chunks.map((chunk, i) => { const data = object(chunk); return <li key={i}>
          <strong>{String(data.question ?? data.section_path ?? '')}</strong> · 分数 {String(data.score ?? '—')}
          <p>{String(data.answer ?? '')}</p>
        </li>; })}</ul>
      </section>;
    })}
    {typeof row.tool_name === 'string' && <p>{row.tool_name} · {String(row.status)} · 重试 {String(row.retry_count)} · {String(row.duration_ms ?? '—')} ms</p>}
    {typeof row.error_message === 'string' && <p>{row.error_message}</p>}
    {typeof row.result_summary === 'string' && <details><summary>工具结果</summary><pre>{row.result_summary}</pre></details>}
  </article>;
}

const questionWords = new Set(['可以', '支持', '是否', '怎么', '如何', '什么', '能否', '多久', '想要', '您好', '你好', '谢谢', '一下', '我们', '你们', '请问', '这个', '那个']);
function sceneKeywords(lines: SceneLine[]): string[] {
  const segmenter = new Intl.Segmenter('zh', { granularity: 'word' });
  return [...new Set(lines.flatMap(line => {
    if (line.lane !== 'customer' || line.channel !== 'api' || line.event !== 'chat') return [];
    return [...segmenter.segment(String(object(line.data).message ?? '').toLowerCase())]
      .filter(item => item.isWordLike && item.segment.length > 1 && !questionWords.has(item.segment))
      .map(item => item.segment);
  }))];
}

export default function OpsRecords({ lines, index }: { lines: SceneLine[]; index: number }) {
  const latest = new Map<string, SceneLine>();
  const reviews = new Map<string, Record<string, unknown>>();
  const approved = new Set<string>();
  for (const line of lines.slice(0, index + 1)) {
    if (line.lane !== 'ops' || line.channel !== 'api') continue;
    const data = object(line.data);
    if (data.response === undefined) continue;
    const values = Array.isArray(data.response) ? data.response : [data.response];
    const reviewRows = values.map(object).filter(row => typeof row.normalized_question === 'string');
    for (const row of reviewRows) {
      const id = String(row.id ?? row.normalized_question);
      reviews.set(id, { ...reviews.get(id), ...row });
    }
    if (line.event === 'approve' && object(data.response).chunk_id !== undefined) {
      const id = String(data.path ?? '').match(/\/review-queue\/([^/]+)\/approve/)?.[1];
      if (id) approved.add(id);
    }
    if (!reviewRows.length && values.length) latest.set(String(data.path ?? line.event), line);
  }
  const keywords = sceneKeywords(lines);
  const matching: Record<string, unknown>[] = [];
  const other: Record<string, unknown>[] = [];
  for (const [id, value] of reviews) {
    const row = approved.has(id) ? { ...value, review_status: '通过' } : value;
    const text = [row.normalized_question, ...(Array.isArray(row.sources) ? row.sources : []).map(source => object(source).raw_question)]
      .join(' ').toLowerCase();
    (row.review_status === '待审' && !keywords.some(word => text.includes(word)) ? other : matching).push(row);
  }
  return <div className={styles.opsRecords}>
    {!latest.size && !reviews.size && <p>等待运营快照…</p>}
    {!!matching.length && <section aria-label="本场景审阅"><h3>本场景审阅</h3>
      {matching.map((row, i) => <RecordView key={String(row.id ?? i)} value={row} />)}
    </section>}
    {!!other.length && <details><summary>另有 {other.length} 条待审（展开）</summary>
      {other.map((row, i) => <RecordView key={String(row.id ?? i)} value={row} />)}
    </details>}
    {[...latest].map(([path, line]) => { const response = object(line.data).response;
      return <section key={path}><h3>{line.event === 'approve' ? '核准结果' : '录制快照'} · {path}</h3>
        {(Array.isArray(response) ? response : [response]).map((value, i) => <RecordView key={i} value={value} />)}
      </section>;
    })}
  </div>;
}
