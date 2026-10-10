import type { NarrationCue } from './scenes';
import styles from './Theater.module.css';
export default function Narration({ cue, language, onLanguage }: {
  cue?: NarrationCue; language: 'zh' | 'en'; onLanguage: (language: 'zh' | 'en') => void;
}) {
  return <aside className={styles.narration} aria-label="场景解说">
    <p aria-live="polite" lang={language === 'zh' ? 'zh-CN' : 'en'}>{cue?.[language] ?? (language === 'zh' ? '播放或选择章节，查看这一刻的解说。' : 'Play or choose a chapter to see what happens at this point.')}</p>
    <div aria-label="解说语言"><button aria-pressed={language === 'zh'} onClick={() => onLanguage('zh')}>中文</button>
      <button aria-pressed={language === 'en'} onClick={() => onLanguage('en')}>EN</button></div>
  </aside>;
}
