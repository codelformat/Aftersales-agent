import { useId, useRef, useState } from 'react';
import type { Citation } from '../protocol/events';
import type { TimelineNode } from './buildTimeline';
import AgentDetail from './details/AgentDetail';
import ContextDetail from './details/ContextDetail';
import GateDetail from './details/GateDetail';
import IntentDetail from './details/IntentDetail';
import ResolveDetail from './details/ResolveDetail';
import RetrievalDetail from './details/RetrievalDetail';
import styles from './Xray.module.css';
const names: Record<string, string> = {
  start_turn: '开始', resolve_reference: '指代消解', classify_intent: '意图识别', retrieve: '检索',
  retrieve_multi: '多查询检索', confidence_gate: '置信度闸', agent_model: 'Agent 推理', agent_tools: '工具执行',
  ensure_order: '选择订单', fetch_order: '读取订单', expand_query: '扩写查询', confirm_write: '确认写入',
  ticket_reply: '工单回复', fallback_reply: '兜底回复', complaint_reply: '投诉安抚', chitchat_reply: '闲聊回复', finalize: '收尾',
};
const states = { running: '进行中', completed: '完成', interrupted: '等待用户', failed: '失败', skipped: '跳过' };
export default function Waterfall({ nodes, highlightedChunkId, citations }: {
  nodes: TimelineNode[]; highlightedChunkId?: number | null; citations?: Citation[];
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [focused, setFocused] = useState(0);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const prefix = useId();
  let step = 0;
  return <ol className={styles.waterfall} aria-label="节点瀑布">{nodes.map((node, index) => {
    if (node.node === 'agent_model') step++;
    const currentStep = step;
    const highlight = highlightedChunkId != null && node.events.some(event => event.kind === 'retrieval' && event.data.top.some(chunk => chunk.chunk_id === highlightedChunkId));
    const open = expanded.has(node.id) || highlight;
    const id = `${prefix}-${node.id}`;
    const agent = node.node === 'agent_model' || node.node === 'agent_tools' || node.events.some(event => event.kind === 'tool');
    return <li key={node.id} className={styles.node}>
      <button type="button" ref={button => { refs.current[index] = button; }} className={styles.row}
        aria-expanded={open} aria-controls={id} tabIndex={focused === index ? 0 : -1} onFocus={() => setFocused(index)}
        onKeyDown={event => {
          if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
          event.preventDefault();
          const next = Math.max(0, Math.min(nodes.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)));
          refs.current[next]?.focus();
        }} onClick={() => setExpanded(previous => {
          const next = new Set(previous); if (next.has(node.id)) next.delete(node.id); else next.add(node.id); return next;
        })}>
        <span className={styles.status} data-status={node.status} aria-live="polite"><span aria-hidden="true">{node.status === 'completed' ? '●' : node.status === 'failed' ? '×' : '○'}</span> {states[node.status]}</span>
        <span className={styles.nodeName}>{names[node.node] ?? node.node}<code>{node.node}</code></span>
        <span className={styles.duration}>{node.durationMs} ms <span aria-hidden="true">{open ? '−' : '+'}</span></span>
        <span className={styles.timing} aria-hidden="true"><span style={{ marginLeft: `${node.totalMs ? node.startMs / node.totalMs * 100 : 0}%`, width: `${node.totalMs ? node.durationMs / node.totalMs * 100 : 0}%` }} /></span>
      </button>
      {open && <div id={id} className={styles.details}>
        {node.events.map((event, i) => {
          switch (event.kind) {
            case 'resolve': return <ResolveDetail key={i} data={event.data} />;
            case 'intent': return <IntentDetail key={i} data={event.data} />;
            case 'retrieval': return <RetrievalDetail key={i} data={event.data} highlightedChunkId={highlightedChunkId} />;
            case 'gate': return <GateDetail key={i} data={event.data} />;
            case 'context': return <ContextDetail key={i} data={event.data} />;
            default: return null;
          }
        })}
        {agent && <AgentDetail events={node.events} step={currentStep} citations={citations} />}
        {!node.events.length && !agent && <p className={styles.detail}>该节点没有详情数据</p>}
      </div>}
    </li>;
  })}</ol>;
}
