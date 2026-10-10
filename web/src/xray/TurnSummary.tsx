import type { TraceEvent } from '../protocol/events';
import styles from './Xray.module.css';
export default function TurnSummary({ trace, totalMs, sessionId, mode }: {
  trace: TraceEvent[]; totalMs: number; sessionId?: string; mode: 'live' | 'replay';
}) {
  const calls = trace.filter(event => event.kind === 'llm');
  const sum = (key: 'input_tokens' | 'output_tokens' | 'cache_read_tokens' | 'reasoning_tokens') => calls.reduce((total, call) => total + call.data[key], 0);
  const intent = trace.filter(event => event.kind === 'intent').at(-1)?.data.intent;
  const langfuse = import.meta.env.VITE_LANGFUSE_URL?.trim().replace(/\/+$/, '');
  return <section className={styles.summary} aria-label="本轮汇总">
    <h3>本轮汇总</h3>
    <dl><div><dt>总耗时</dt><dd>{totalMs} ms</dd></div><div><dt>LLM 次数</dt><dd aria-label="LLM 次数">{calls.length}</dd></div>
      <div><dt>输入 token</dt><dd aria-label="输入 token">{sum('input_tokens')}</dd></div>
      <div><dt>输出 token</dt><dd aria-label="输出 token">{sum('output_tokens')}</dd></div>
      <div><dt>缓存 token</dt><dd aria-label="缓存 token">{sum('cache_read_tokens')}</dd></div>
      <div><dt>思考 token</dt><dd aria-label="思考 token">{sum('reasoning_tokens')}</dd></div>
    </dl><p>意图：{intent ?? '未提供'}</p>
    {mode === 'live' && langfuse && sessionId && <a href={`${langfuse}/project/aftersales/sessions/${encodeURIComponent(sessionId)}`} target="_blank" rel="noopener noreferrer">Langfuse ↗</a>}
  </section>;
}
