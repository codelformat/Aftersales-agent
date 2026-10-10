import { describe, expect, it } from 'vitest';
import { buildTimeline } from './buildTimeline';
import { end, llm, resolve, start, tool } from './test-fixtures';

describe('buildTimeline', () => {
  it('pairs repeated node occurrences without merging their events', () => {
    const nodes = buildTimeline([start('agent_model', 0), llm, end('agent_model', 120, 120),
      start('agent_model', 130), { ...llm, t_ms: 150 }, end('agent_model', 160, 30)]);
    expect(nodes).toHaveLength(2);
    expect(nodes[0]).toMatchObject({ node: 'agent_model', startMs: 0, durationMs: 120, totalMs: 160, status: 'completed', events: [llm] });
    expect(nodes[1]).toMatchObject({ startMs: 130, durationMs: 30, events: [{ ...llm, t_ms: 150 }] });
    expect(nodes[0].id).not.toBe(nodes[1].id);
  });
  it('attaches nested domain events by their node and accepts late LLM usage', () => {
    const nodes = buildTimeline([start('resolve_reference', 0), start('agent_model', 5), resolve,
      end('agent_model', 30, 25), { ...llm, t_ms: 35 }, end('resolve_reference', 40, 40)]);
    expect(nodes[0].events).toEqual([resolve]);
    expect(nodes[1].events).toEqual([{ ...llm, t_ms: 35 }]);
    expect(nodes.map(node => node.totalMs)).toEqual([40, 40]);
  });
  it('keeps unfinished nodes running and measures them to the latest trace', () => {
    const nodes = buildTimeline([start('resolve_reference', 0), resolve]);
    expect(nodes[0]).toMatchObject({ status: 'running', durationMs: 10, totalMs: 10 });
  });
  it('marks interrupt and creates a new occurrence on resume with reset timestamps', () => {
    const nodes = buildTimeline([start('ensure_order', 0), end('ensure_order', 100, 100, true),
      start('ensure_order', 0), end('ensure_order', 60, 60)]);
    expect(nodes[0]).toMatchObject({ status: 'interrupted', interrupted: true, totalMs: 160 });
    expect(nodes[1]).toMatchObject({ status: 'completed', startMs: 100, durationMs: 60, totalMs: 160 });
  });
  it('attaches nullable tool nodes to the active node and leaves input untouched', () => {
    const trace = [start('agent_tools', 0), { ...tool, node: null }];
    const before = structuredClone(trace);
    expect(buildTimeline(trace)[0].events).toEqual([{ ...tool, node: null }]);
    expect(trace).toEqual(before);
  });
  it('handles empty traces', () => { expect(buildTimeline([])).toEqual([]); });
});
