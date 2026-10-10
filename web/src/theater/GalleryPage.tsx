import { scenes } from './scenes';
import styles from './Theater.module.css';
export default function GalleryPage() {
  return <section><h1>场景库</h1><p>从实际会话看系统如何处理证据、业务操作和失败。</p>
    <div className={styles.gallery}>{scenes.map(scene => <article key={scene.id} className={styles.card}>
      <span className={styles.eyebrow}>{scene.layout === 'strategies' ? '评估快照 · 解说 12 s' : `真实录制 · ${(scene.durationMs / 1000).toFixed(1)} s`}</span>
      <h2>{scene.titleZh}</h2><p lang="en">{scene.titleEn}</p><p>{scene.proves}</p>
      <ul className={styles.tags} aria-label="场景标签">{scene.tags.map(tag => <li key={tag}>{tag}</li>)}</ul>
      <a href={`#/theater/${scene.id}`} aria-label={`观看 ${scene.titleZh}`}>观看 →</a>
    </article>)}</div>
  </section>;
}
