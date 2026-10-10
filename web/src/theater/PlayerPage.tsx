import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useViewMode } from '../app/ViewModeContext';
import { REPO_URL } from '../config';
import { ReplayClock } from '../data/clock';
import type { Scene, StrategyComparison } from '../data/DataSource';
import { ReplayDataSource } from '../data/replay';
import { parseDeepLink, formatDeepLink } from './deepLink';
import type { TheaterLink } from './deepLink';
import { currentCue, resolveCues } from './narration';
import Narration from './Narration.tsx';
import OpsRecords from './OpsRecords';
import RecordedDesk from './RecordedDesk';
import { scenes } from './scenes';
import type { SceneMetadata } from './scenes';
import { strategyScene } from './sceneData';
import SplitStage from './SplitStage';
import StrategyStage from './StrategyStage';
import Timeline from './Timeline';
import styles from './Theater.module.css';

class SnapshotSource extends ReplayDataSource {
  constructor(private readonly snapshot: StrategyComparison) { super(); }
  override async strategyComparison() { return this.snapshot; }
}
interface LoadedScene { scene: Scene; metadata: SceneMetadata; source: ReplayDataSource; initial: TheaterLink }
function readAutoHold() {
  try { return localStorage.getItem('theater_auto_hold') !== 'false'; }
  catch { return true; }
}
function Playback({ scene, metadata, source, initial }: LoadedScene) {
  const { viewMode, setViewMode } = useViewMode();
  const [autoHold, setAutoHold] = useState(readAutoHold);
  const player = useRef<HTMLElement>(null);
  const [{ clock, resolved, start }] = useState(() => {
    const resolved = resolveCues(scene.lines, metadata.narration);
    const clock = new ReplayClock(scene.lines, { holdIndices: resolved.map(item => item.index), autoHold });
    clock.seek(initial.seconds * 1000);
    let start = { index: -1, position: clock.position() };
    const off = clock.onTick((index, position) => { start = { index, position }; });
    off();
    return { clock, resolved, start };
  });
  const [tick, setTick] = useState(start);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [language, setLanguage] = useState<'zh' | 'en'>(initial.lang ?? 'zh');
  useLayoutEffect(() => {
    const element = player.current;
    if (!element) return;
    const container = element.closest('main');
    const topBar = container?.previousElementSibling;
    function fitViewport() {
      const surrounding = container ? getComputedStyle(container) : undefined;
      const bottom = surrounding ? parseFloat(surrounding.paddingBottom) + parseFloat(surrounding.borderBottomWidth) + parseFloat(surrounding.marginBottom) : 24;
      const documentTop = element!.getBoundingClientRect().top + window.scrollY;
      element!.style.setProperty('--player-height', `${Math.max(0, window.innerHeight - documentTop - bottom)}px`);
      element!.style.setProperty('--narration-top', `${topBar ? topBar.getBoundingClientRect().bottom + window.scrollY : 0}px`);
    }
    fitViewport();
    const observer = new ResizeObserver(fitViewport);
    if (topBar) observer.observe(topBar);
    window.addEventListener('resize', fitViewport);
    return () => { observer.disconnect(); window.removeEventListener('resize', fitViewport); };
  }, []);
  useEffect(() => {
    const off = clock.onTick((index, position) => {
      setTick({ index, position });
      if (position >= clock.durationMs) setPlaying(false);
    });
    return () => { off(); clock.pause(); };
  }, [clock]);
  useEffect(() => {
    if (initial.autoplay && clock.position() < clock.durationMs) { clock.play(speed); setPlaying(true); }
  }, [clock, initial.autoplay]);
  useEffect(() => {
    const onHashChange = () => {
      const link = parseDeepLink(window.location.hash);
      if (link?.scene !== metadata.id) return;
      clock.seek(link.seconds * 1000);
      setViewMode(link.view === 'cust' ? 'customer' : 'eng');
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [clock, metadata.id, setViewMode]);
  function replaceLink(ms: number) {
    window.history.replaceState(window.history.state, '', formatDeepLink({ scene: metadata.id, seconds: ms / 1000,
      view: viewMode === 'customer' ? 'cust' : 'eng', ...(language === 'en' ? { lang: 'en' as const } : {}) }));
  }
  useEffect(() => {
    window.history.replaceState(window.history.state, '', formatDeepLink({ scene: metadata.id, seconds: clock.position() / 1000,
      view: viewMode === 'customer' ? 'cust' : 'eng', ...(language === 'en' ? { lang: 'en' as const } : {}) }));
  }, [clock, metadata.id, viewMode, language]);
  function seek(ms: number) { clock.seek(ms); replaceLink(clock.position()); }
  const stageProps = { lines: scene.lines, index: tick.index, engineering: viewMode === 'eng', source };
  return <section className={styles.player} ref={player}>
    <header className={styles.playerHeading}><a href="#/theater">← 场景库</a><h1>{metadata.titleZh}</h1><p lang="en">{metadata.titleEn}</p></header>
    <Narration cue={currentCue(resolved, tick.index)} language={language} onLanguage={setLanguage} />
    <div className={styles.stage}>
      {metadata.layout === 'strategies' ? <StrategyStage source={source} /> : metadata.layout === 'split' ?
        <SplitStage {...stageProps} /> : <>
          <RecordedDesk {...stageProps} />
          {scene.lines.some(line => line.lane === 'ops' && line.channel === 'api' && 'response' in Object(line.data)) &&
            <details className={styles.audit}><summary>运营录制快照</summary><OpsRecords lines={scene.lines} index={tick.index} /></details>}
        </>}
    </div>
    <Timeline clock={clock} resolved={resolved} position={tick.position} playing={playing} speed={speed} language={language}
      autoHold={autoHold} onAutoHold={enabled => {
        clock.setAutoHold(enabled); setAutoHold(enabled);
        try { localStorage.setItem('theater_auto_hold', String(enabled)); } catch { /* Keep the preference in memory. */ }
        replaceLink(clock.position());
      }}
      onSeek={seek} onPlay={() => {
        if (playing) { clock.pause(); setPlaying(false); }
        else { if (clock.position() >= clock.durationMs) seek(0); clock.play(speed); setPlaying(true); }
      }} onSpeed={value => { clock.setSpeed(value); setSpeed(value); }}
      onSkip={() => { const next = resolved.find(item => clock.timeAt(item.index) > clock.position()); if (next) seek(clock.timeAt(next.index)); }} />
    <footer className={styles.footer}>
      {metadata.layout === 'strategies' ? '评估日期' : '录制于'} {scene.header.recorded_at.slice(0, 10)} · commit{' '}
      <a href={`${REPO_URL}/commit/${scene.header.git_commit}`} target="_blank" rel="noreferrer">{scene.header.git_commit.slice(0, 7)}</a>
      {' · '}{scene.header.model}
    </footer>
  </section>;
}
export default function PlayerPage({ sceneId }: { sceneId: string }) {
  const { setViewMode } = useViewMode();
  const source = useMemo(() => new ReplayDataSource(), []);
  const metadata = scenes.find(scene => scene.id === sceneId);
  const [loaded, setLoaded] = useState<LoadedScene>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setLoaded(undefined); setError('');
    if (!metadata) return;
    const initial = parseDeepLink(window.location.hash) ?? { scene: sceneId, seconds: 0, view: 'eng' as const };
    setViewMode(initial.view === 'cust' ? 'customer' : 'eng');
    async function load() {
      try {
        let scene: Scene;
        let sceneSource = source;
        if (metadata!.layout === 'strategies') {
          const snapshot = await source.strategyComparison();
          scene = strategyScene(snapshot);
          sceneSource = new SnapshotSource(snapshot);
        } else scene = await source.loadScene(sceneId);
        resolveCues(scene.lines, metadata!.narration);
        if (active) setLoaded({ scene, metadata: metadata!, source: sceneSource, initial });
      } catch (reason) {
        if (active) setError(`录制加载失败：${reason instanceof Error ? reason.message : '未知错误'}`);
      }
    }
    void load();
    return () => { active = false; };
  }, [sceneId, metadata, source, setViewMode]);
  if (!metadata || error) return <section><h1>场景播放器</h1><p role="alert">{error || '场景不存在'}</p><a href="#/theater">返回场景库</a></section>;
  if (!loaded || loaded.metadata.id !== sceneId) return <section><h1>{metadata.titleZh}</h1><p role="status">正在加载录制…</p></section>;
  return <Playback key={sceneId} {...loaded} />;
}
