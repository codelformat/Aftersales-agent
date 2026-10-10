import type { ReplayClock } from '../data/clock';
import type { ResolvedCue } from './narration';
import styles from './Theater.module.css';
export default function Timeline({ clock, resolved, position, playing, speed, language, autoHold, onAutoHold, onSeek, onPlay, onSpeed, onSkip }: {
  clock: ReplayClock; resolved: ResolvedCue[]; position: number; playing: boolean; speed: number;
  language: 'zh' | 'en'; onSeek: (ms: number) => void; onPlay: () => void;
  onSpeed: (speed: number) => void; onSkip: () => void;
  autoHold: boolean; onAutoHold: (enabled: boolean) => void;
}) {
  const next = resolved.find(item => clock.timeAt(item.index) > position);
  return <section className={styles.timeline} aria-label="回放控制">
    <div className={styles.track}>
      {!!clock.holds.length && <div className={styles.holds} aria-label="解说停留区间">{clock.holds.map(hold => <span key={hold.index}
        className={styles.hold} title="解说停留 3 秒" aria-label={`解说停留：${(hold.atMs / 1000).toFixed(2)} 秒起，持续 3 秒`}
        style={{ left: `${hold.atMs / clock.durationMs * 100}%`, width: `${hold.durationMs / clock.durationMs * 100}%` }} />)}</div>}
      <input type="range" aria-label="播放进度" min={0} max={clock.durationMs} step={1} value={position}
        onChange={event => onSeek(Number(event.target.value))} aria-valuetext={`${(position / 1000).toFixed(2)} / ${(clock.durationMs / 1000).toFixed(2)} 秒`} />
      <div className={styles.chapters}>{resolved.map((item, i) => <button key={i} className={styles.chapter}
        style={{ left: `${clock.durationMs ? clock.timeAt(item.index) / clock.durationMs * 100 : 0}%` }}
        title={item.cue[language]} aria-label={`章节 ${i + 1}：${item.cue[language]}`} onClick={() => onSeek(clock.timeAt(item.index))}>{i + 1}</button>)}</div>
    </div>
    {!!clock.gaps.length && <div className={styles.gaps} aria-label="压缩等待">{clock.gaps.map((gap, index) => <span key={index}>
      {(gap.atMs / 1000).toFixed(2)} s · <strong>⏩ 实际 {gap.realMs / 1000} s</strong>
    </span>)}</div>}
    <div className={styles.controls}>
      <button onClick={onPlay}>{playing ? '暂停' : '播放'}</button>
      <label>播放速度 <select aria-label="播放速度" value={speed} onChange={event => onSpeed(Number(event.target.value))}>
        {[0.5, 1, 2, 4].map(value => <option key={value} value={value}>{value}×</option>)}
      </select></label>
      <label><input type="checkbox" checked={autoHold} onChange={event => onAutoHold(event.target.checked)} /> 自动停留</label>
      <button onClick={onSkip} disabled={!next}>跳过等待</button>
      <output>{(position / 1000).toFixed(2)} / {(clock.durationMs / 1000).toFixed(2)} s</output>
    </div>
  </section>;
}
