import type { Citation, TraceEvent } from '../../protocol/events';
import styles from '../Xray.module.css';
export default function AgentDetail({ events, step, citations = [] }: { events: TraceEvent[]; step: number; citations?: Citation[] }) {
  const tools = events.filter(event => event.kind === 'tool');
  const calls = events.filter(event => event.kind === 'llm');
  return <section className={styles.detail} aria-label="Agent 详情">
    <p>{step > 0 ? `第 ${step} 步` : '工具执行'}</p>
    {calls.map((event, i) => <p key={i}>{event.data.model} · {event.data.ms} ms</p>)}
    {tools.map((event, i) => <div className={styles.tool} key={`${event.data.call_id}-${i}`}>
      <strong>{event.data.name}</strong> <span className={styles.chip}>{event.data.source === 'builtin' ? '内置' : `MCP · ${event.data.mcp_server ?? '未知服务器'}`}</span>
      <p className={styles.status} data-status={event.data.status === '成功' ? 'completed' : 'failed'}>{event.data.status} · 重试 {event.data.retry_count} · {event.data.duration_ms} ms</p>
      {event.data.error_message && <p>{event.data.error_message}</p>}
    </div>)}
    {!!citations.length && <><h3>引用映射</h3>{citations.map(citation => <p key={citation.n}>[{citation.n}] → chunk #{citation.chunk_id} · {citation.section_path}</p>)}</>}
  </section>;
}
