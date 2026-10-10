import type { SceneLine } from './DataSource';

export interface ReplayClockOptions {
  gapThresholdMs?: number;
  compressedGapMs?: number;
  now?: () => number;
  // Schedule once and return a cancellation function. Both sources can be injected.
  schedule?: (cb: () => void, delayMs: number) => () => void;
}
type Tick = (indexInclusive: number, positionMs: number) => void;

export class ReplayClock {
  readonly durationMs: number;
  readonly gaps: { atMs: number; realMs: number }[] = [];
  private readonly times: number[] = [];
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
        this.gaps.push({ atMs: elapsed, realMs: gap });
        elapsed += compressed;
      } else {
        elapsed += gap;
      }
      this.times.push(elapsed);
      previous = line.t_ms;
    }
    this.durationMs = elapsed;
    this.now = opts.now ?? (() => performance.now());
    this.schedule = opts.schedule ?? ((cb, delayMs) => {
      const id = setTimeout(cb, delayMs);
      return () => clearTimeout(id);
    });
  }

  position(): number {
    if (!this.playing) return this.basePosition;
    return Math.min(this.durationMs, this.basePosition + Math.max(0, this.now() - this.baseNow) * this.speed);
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
    let high = this.times.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (this.times[middle] <= position) low = middle + 1;
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
