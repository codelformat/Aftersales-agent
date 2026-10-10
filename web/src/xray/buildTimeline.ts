import type { TraceEvent } from '../protocol/events';
export interface TimelineNode {
  id: string;
  node: string;
  startMs: number;
  durationMs: number;
  totalMs: number;
  status: 'running' | 'completed' | 'interrupted' | 'failed' | 'skipped';
  interrupted: boolean;
  events: TraceEvent[];
}

/** 每次 node_start 是独立节点；恢复请求的时钟从前一请求末尾接续。 */
export function buildTimeline(trace: TraceEvent[]): TimelineNode[] {
  const nodes: TimelineNode[] = [];
  let offset = 0;
  let previous = 0;
  let totalMs = 0;
  for (const event of trace) {
    if (event.t_ms < previous) offset = totalMs;
    previous = event.t_ms;
    const time = offset + event.t_ms;
    totalMs = Math.max(totalMs, time);
    if (event.kind === 'node_start') {
      nodes.push({ id: `${event.node}-${nodes.length}`, node: event.node, startMs: time,
        durationMs: 0, totalMs: 0, status: 'running', interrupted: false, events: [] });
      continue;
    }
    const candidates = nodes.filter(node => event.node === null || node.node === event.node);
    const node = [...candidates].reverse().find(node => node.status === 'running') ?? candidates.at(-1);
    if (!node) continue;
    if (event.kind === 'node_end') {
      // 一个 end 只关闭一个 start；LLM 事件可以晚于 node_end 到达。
      if (node.status !== 'running') continue;
      node.durationMs = event.data.ms;
      node.interrupted = event.data.interrupted === true;
      node.status = node.interrupted ? 'interrupted' : 'completed';
      totalMs = Math.max(totalMs, node.startMs + node.durationMs);
    } else {
      node.events.push(event);
    }
  }
  return nodes.map(node => ({ ...node, totalMs,
    durationMs: node.status === 'running' ? Math.max(0, totalMs - node.startMs) : node.durationMs }));
}
