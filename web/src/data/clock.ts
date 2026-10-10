import type { SceneLine } from './DataSource';

export interface ReplayClockOptions {
  gapThresholdMs?: number;
  compressedGapMs?: number;
  holdIndices?: readonly number[];
  autoHold?: boolean;
  now?: () => number;
  // Schedule once and return a cancellation function. Both sources can be injected.
  schedule?: (cb: () => void, delayMs: number) => () => void;
}
type Tick = (indexInclusive: number, positionMs: number) => void;

/** Pure mapping from compressed recording time to playback time with inserted holds. */
export function playbackMapping(times: readonly number[], holdIndices: readonly number[] = []) {
  const anchors = new Set(holdIndices);
  for (const index of anchors) {
    if (!Number.isInteger(index) || index < 0 || index >= times.length) throw new RangeError('Hold index is outside the recording');
  }
  const holds: { index: number; atMs: number; durationMs: number }[] = [];
  let offset = 0;
  const eventTimes = times.map((time, index) => {
    const atMs = time + offset;
    if (anchors.has(index)) {
      holds.push({ index, atMs, durationMs: 3000 });
      offset += 3000;
    }
    return atMs;
  });
  return {
    eventTimes, holds, durationMs: (times.at(-1) ?? 0) + offset,
    toRecording(position: number): number {
      let held = 0;
      for (const hold of holds) held += Math.max(0, Math.min(hold.durationMs, position - hold.atMs));
      return position - held;
    },
    toPlayback(position: number, indexInclusive = -1): number {
      return position + holds.reduce((sum, hold) => sum + (
        times[hold.index] < position || (times[hold.index] === position && hold.index < indexInclusive) ? hold.durationMs : 0
      ), 0);
    },
  };
}

export class ReplayClock {
  private readonly recordingGaps: { atMs: number; realMs: number; index: number }[] = [];
  private readonly times: number[] = [];
  private readonly holdIndices: readonly number[];
  private mapping: ReturnType<typeof playbackMapping>;
  private autoHold: boolean;
  private readonly now: () => number;
  private readonly schedule: NonNullable<ReplayClockOptions['schedule']>;
  private readonly listeners = new Set<Tick>();
  private basePosition = 0;
  private baseNow = 0;
  private speed = 1;
  private playing = false;
  private cancelTick?: () => void;

  constructor(lines: SceneLine[], opts: ReplayClockOptions = {}) {
    const threshold = opts.gapThresholdMs ?? 3000;
    const compressed = opts.compressedGapMs ?? 1500;
    let previous = 0;
    let elapsed = 0;
    for (const line of lines) {
      const gap = line.t_ms - previous;
      if (gap > threshold) {
        this.recordingGaps.push({ atMs: elapsed, realMs: gap, index: this.times.length });
        elapsed += compressed;
      } else {
        elapsed += gap;
      }
      this.times.push(elapsed);
      previous = line.t_ms;
    }
    this.holdIndices = opts.holdIndices ?? [];
    this.autoHold = opts.autoHold ?? true;
    this.mapping = playbackMapping(this.times, this.autoHold ? this.holdIndices : []);
    this.now = opts.now ?? (() => performance.now());
    this.schedule = opts.schedule ?? ((cb, delayMs) => {
      const id = setTimeout(cb, delayMs);
      return () => clearTimeout(id);
    });
  }

  get durationMs(): number { return this.mapping.durationMs; }
  get holds() { return this.mapping.holds; }
  get gaps() {
    return this.recordingGaps.map(gap => ({
      atMs: gap.atMs + this.holds.filter(hold => hold.index < gap.index).reduce((sum, hold) => sum + hold.durationMs, 0),
      realMs: gap.realMs,
    }));
  }

  setAutoHold(enabled: boolean): void {
    if (enabled === this.autoHold) return;
    const position = this.position();
    const index = this.indexAt(position);
    const recordingPosition = this.mapping.toRecording(position);
    this.autoHold = enabled;
    this.mapping = playbackMapping(this.times, enabled ? this.holdIndices : []);
    this.seek(this.mapping.toPlayback(recordingPosition, index));
  }

  position(): number {
    if (!this.playing) return this.basePosition;
    return Math.min(this.durationMs, this.basePosition + Math.max(0, this.now() - this.baseNow) * this.speed);
  }

  timeAt(index: number): number {
    const time = this.mapping.eventTimes[index];
    if (time === undefined) throw new RangeError('Event index is outside the recording');
    return time;
  }

  play(speed?: number): void {
    if (speed !== undefined) this.setSpeed(speed);
    if (this.playing || this.basePosition >= this.durationMs) return;
    this.baseNow = this.now();
    this.playing = true;
    this.emit();
    this.queueTick();
  }

  pause(): void {
    this.basePosition = this.position();
    this.stop();
    this.emit();
  }

  seek(ms: number): void {
    if (!Number.isFinite(ms)) throw new RangeError('Seek position must be finite');
    this.basePosition = Math.max(0, Math.min(this.durationMs, ms));
    this.baseNow = this.now();
    if (this.basePosition >= this.durationMs) this.stop();
    this.emit();
    this.queueTick();
  }

  setSpeed(speed: number): void {
    if (!Number.isFinite(speed) || speed < 0.5 || speed > 4) throw new RangeError('Speed must be between 0.5 and 4');
    this.basePosition = this.position();
    this.baseNow = this.now();
    this.speed = speed;
    this.emit();
  }

  onTick(cb: Tick): () => void {
    this.listeners.add(cb);
    const position = this.position();
    cb(this.indexAt(position), position);
    return () => { this.listeners.delete(cb); };
  }

  private stop(): void {
    this.playing = false;
    this.cancelTick?.();
    this.cancelTick = undefined;
  }

  private indexAt(position: number): number {
    // Upper bound also includes every event sharing the current timestamp.
    let low = 0;
    let high = this.mapping.eventTimes.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (this.mapping.eventTimes[middle] <= position) low = middle + 1;
      else high = middle;
    }
    return low - 1;
  }

  private emit(): void {
    const position = this.position();
    if (this.playing && position >= this.durationMs) {
      this.basePosition = this.durationMs;
      this.stop();
    }
    const index = this.indexAt(position);
    for (const listener of [...this.listeners]) listener(index, position);
  }

  private queueTick(): void {
    if (!this.playing || this.cancelTick) return;
    this.cancelTick = this.schedule(() => {
      this.cancelTick = undefined;
      this.emit();
      this.queueTick();
    }, 16);
  }
}
