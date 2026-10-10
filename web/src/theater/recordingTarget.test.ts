import { describe, expect, it } from 'vitest';
import { gateCompletion } from '../../e2e/recording';
import type { SceneLine } from '../data/DataSource';
import { recording } from './test-fixtures';

const recorded = recording('multi-turn').lines;
const chat = recorded.find(line => line.channel === 'api' && line.event === 'chat')!;
const gate = recorded.find(line => line.channel === 'sse' && line.event === 'trace' && line.data.kind === 'gate')!;
const done = recorded.find(line => line.channel === 'sse' && line.event === 'done')!;
// Deliberately different timestamps, long gaps, and a done/chat collision.
const lines: SceneLine[] = [
  { ...chat, t_ms: 0 }, { ...gate, t_ms: 10000 }, { ...done, t_ms: 10003 },
  { ...chat, t_ms: 10003 }, { ...gate, t_ms: 20000 }, { ...done, t_ms: 20007 },
];

describe('gate screenshot event targets', () => {
  it('maps the first completed gate through gap compression and retains its turn at a timestamp collision', () => {
    expect(gateCompletion(lines, 1)).toEqual({ position: 1503, turn: 1 });
  });
  it('finds the second gate completion independently of the recording timestamps', () => {
    expect(gateCompletion(lines, 2)).toEqual({ position: 3010, turn: 2 });
  });
  it('ignores ops lane gate and done events', () => {
    const ops: SceneLine[] = [
      { ...gate, lane: 'ops', t_ms: 0 }, { ...done, lane: 'ops', t_ms: 0 }, ...lines,
    ];
    expect(gateCompletion(ops, 1)).toEqual({ position: 1503, turn: 1 });
  });
  it('fails clearly when the requested gate or its completion is absent', () => {
    expect(() => gateCompletion(lines, 3)).toThrow('Recording has no gate #3');
    expect(() => gateCompletion(lines.slice(0, 2), 1)).toThrow('Recording has no done after gate #1');
    expect(() => gateCompletion(lines, 0)).toThrow('Gate occurrence must be positive');
  });
});
