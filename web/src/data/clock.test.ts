// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { ReplayClock } from './clock';
import type { SceneLine } from './DataSource';

const lines = (times: number[]): SceneLine[] => times.map(t_ms => ({
  t_ms, lane: 'customer', channel: 'sse', event: 'token', data: { text: '字' },
}));
function timeSource() {
  let time = 0;
  const pending = new Set<() => void>();
  return {
    now: () => time,
    elapse: (ms: number) => { time += ms; },
    schedule: (cb: () => void, _delayMs: number) => {
      pending.add(cb);
      return () => { pending.delete(cb); };
    },
    advance: (ms: number) => {
      time += ms;
      for (const cb of [...pending]) { pending.delete(cb); cb(); }
    },
    pending: () => pending.size,
  };
}

describe('ReplayClock', () => {
  it('compresses only gaps over the threshold and records compressed markers', () => {
    const clock = new ReplayClock(lines([0, 1000, 7000, 7500]));
    expect(clock.durationMs).toBe(3000);
    expect(clock.gaps).toEqual([{ atMs: 1000, realMs: 6000 }]);
    expect(new ReplayClock(lines([0, 3000])).gaps).toEqual([]);
    expect(new ReplayClock(lines([0, 5000]), { gapThresholdMs: 1000, compressedGapMs: 200 }).durationMs).toBe(200);
  });
  it('compresses initial waiting and counts simultaneous events inclusively', () => {
    const clock = new ReplayClock(lines([6000, 6000, 6500]));
    const ticks: number[][] = [];
    clock.onTick((i, p) => ticks.push([i, p]));
    expect(clock.gaps).toEqual([{ atMs: 0, realMs: 6000 }]);
    expect(clock.durationMs).toBe(2000);
    expect(ticks.at(-1)).toEqual([-1, 0]);
    clock.seek(1500);
    expect(ticks.at(-1)).toEqual([1, 1500]);
  });
  it('advances at 2× speed with injected time and schedule', () => {
    const time = timeSource();
    const clock = new ReplayClock(lines([0, 1000, 2000]), time);
    const ticks: number[][] = [];
    clock.onTick((i, p) => ticks.push([i, p]));
    clock.play(2);
    time.advance(500);
    expect(clock.position()).toBe(1000);
    expect(ticks.at(-1)).toEqual([1, 1000]);
    clock.pause();
  });
  it('pauses without advancing and resumes from the same position', () => {
    const time = timeSource();
    const clock = new ReplayClock(lines([0, 1000, 3000]), time);
    clock.play();
    time.advance(400);
    clock.pause();
    time.advance(9000);
    expect(clock.position()).toBe(400);
    expect(time.pending()).toBe(0);
    clock.play();
    time.advance(600);
    expect(clock.position()).toBe(1000);
    clock.pause();
  });
  it('rewinds the inclusive index when seeking backward, including while playing', () => {
    const time = timeSource();
    const clock = new ReplayClock(lines([0, 1000, 2000]), time);
    const ticks: number[][] = [];
    const unsubscribe = clock.onTick((i, p) => ticks.push([i, p]));
    clock.play();
    time.advance(1500);
    expect(ticks.at(-1)).toEqual([1, 1500]);
    clock.seek(500);
    expect(ticks.at(-1)).toEqual([0, 500]);
    time.advance(250);
    expect(ticks.at(-1)).toEqual([0, 750]);
    unsubscribe();
    time.advance(100);
    expect(ticks.at(-1)).toEqual([0, 750]);
    clock.pause();
  });
  it('automatically pauses at the end and does not retain a scheduled tick', () => {
    const time = timeSource();
    const clock = new ReplayClock(lines([0, 1000]), time);
    const ticks: number[][] = [];
    clock.onTick((i, p) => ticks.push([i, p]));
    clock.play();
    time.advance(1500);
    expect(ticks.at(-1)).toEqual([1, 1000]);
    expect(time.pending()).toBe(0);
    time.advance(9000);
    expect(clock.position()).toBe(1000);
    clock.seek(0);
    time.advance(500);
    expect(clock.position()).toBe(0);
  });
  it('delivers the final event even when position is read before the scheduled tick', () => {
    const time = timeSource();
    const clock = new ReplayClock(lines([0, 1000]), time);
    const ticks: number[][] = [];
    clock.onTick((i, p) => ticks.push([i, p]));
    clock.play();
    time.elapse(1000);
    expect(clock.position()).toBe(1000);
    time.advance(0);
    expect(ticks.at(-1)).toEqual([1, 1000]);
    expect(time.pending()).toBe(0);
  });
  it('selects event indices using compressed rather than original timestamps', () => {
    const clock = new ReplayClock(lines([0, 1000, 7000, 7500]));
    const ticks: number[][] = [];
    clock.onTick((i, p) => ticks.push([i, p]));
    clock.seek(2499);
    expect(ticks.at(-1)).toEqual([1, 2499]);
    clock.seek(2500);
    expect(ticks.at(-1)).toEqual([2, 2500]);
    clock.seek(1000);
    expect(ticks.at(-1)).toEqual([1, 1000]);
  });
  it('keeps elapsed progress when changing speed between scheduled ticks', () => {
    const time = timeSource();
    const clock = new ReplayClock(lines([0, 3000]), time);
    clock.play();
    time.advance(400);
    clock.setSpeed(2);
    clock.play();
    time.advance(300);
    expect(clock.position()).toBe(1000);
    expect(time.pending()).toBe(1);
    clock.pause();
  });
  it('clamps seeks and handles empty recordings without scheduling', () => {
    const time = timeSource();
    const empty = new ReplayClock([], time);
    empty.play();
    expect(empty.durationMs).toBe(0);
    expect(empty.position()).toBe(0);
    expect(time.pending()).toBe(0);
    const clock = new ReplayClock(lines([0, 1000]), time);
    clock.seek(-10);
    expect(clock.position()).toBe(0);
    clock.seek(9000);
    expect(clock.position()).toBe(1000);
  });
  it.each([0, -1, NaN, Infinity, 0.25, 5])('rejects invalid speed %s', speed => {
    expect(() => new ReplayClock(lines([0, 1000])).setSpeed(speed)).toThrow(RangeError);
  });
});
