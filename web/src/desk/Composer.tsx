import { useEffect, useRef, useState } from 'react';
import styles from './Desk.module.css';

export default function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => Promise<void> }) {
  const [text, setText] = useState('');
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (!disabled) input.current?.focus(); }, [disabled]);
  function submit() {
    if (disabled || !text.trim()) return;
    setText(''); void onSend(text);
  }
  return <form className={styles.composer} onSubmit={event => { event.preventDefault(); submit(); }}>
    <div className={styles.inputRow}>
      <textarea ref={input} aria-label="输入您的问题" aria-describedby="desk-input-hint" rows={2} maxLength={2000}
        placeholder="输入问题或订单号…" value={text} disabled={disabled} onChange={event => setText(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
            event.preventDefault(); submit();
          }
        }} />
      <button type="submit" className={styles.primary} disabled={disabled || !text.trim()}>发送</button>
    </div>
    <p id="desk-input-hint" className={styles.hint}>Enter 发送 · Shift+Enter 换行 · 最多 2000 字符</p>
  </form>;
}
