import { useId, useState } from 'react';
import type { Turn } from '../state/conversation';
import { buildTimeline } from './buildTimeline';
import TurnSummary from './TurnSummary';
import Waterfall from './Waterfall';
import styles from './Xray.module.css';
export interface XrayPanelProps {
  turn?: Turn; mode: 'live' | 'replay'; sessionId?: string; highlightedChunkId?: number | null;
  collapsed?: boolean; onCollapseChange?: (collapsed: boolean) => void;
}
export default function XrayPanel({ turn, mode, sessionId, highlightedChunkId, collapsed: controlledCollapsed, onCollapseChange }: XrayPanelProps) {
  const [localCollapsed, setCollapsed] = useState(false);
  const collapsed = controlledCollapsed ?? localCollapsed;
  const id = useId();
  const nodes = buildTimeline(turn?.trace ?? []).map(node => turn?.status === 'error' && node.status === 'running' ? { ...node, status: 'failed' as const } : node);
  const totalMs = nodes[0]?.totalMs ?? Math.max(0, ...(turn?.trace ?? []).map(event => event.t_ms));
  return <div className={styles.panel} data-collapsed={collapsed}>
    <header className={styles.heading}><h2>透视面板</h2>
      <button type="button" aria-label={collapsed ? '展开透视面板' : '收起透视面板'} aria-expanded={!collapsed} aria-controls={id}
        onClick={() => { const next = !collapsed; setCollapsed(next); onCollapseChange?.(next); }}>{collapsed ? '‹' : '›'}</button>
    </header>
    {!collapsed && <div id={id} className={styles.content}>
      <p className={styles.live} role="status" aria-live="polite">{turn ? ({ streaming: '本轮进行中', done: '本轮完成', interrupted: '本轮等待用户', error: '本轮失败' }[turn.status]) : '尚未选择轮次'}</p>
      {!turn?.trace.length ? <p className={styles.empty}>该轮没有调试数据</p> : <>
        <Waterfall key={turn.id} nodes={nodes} highlightedChunkId={highlightedChunkId} citations={turn.citations} />
        <TurnSummary trace={turn.trace} totalMs={totalMs} sessionId={sessionId} mode={mode} />
      </>}
    </div>}
  </div>;
}
