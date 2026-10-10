import { useRef, useState } from 'react';
import type { DataSource } from '../../data/DataSource';
import styles from '../Desk.module.css';

export default function Feedback({ source, sessionId, userId, messageId, answerIndex }: {
  source: DataSource; sessionId: string; userId: string; messageId: number; answerIndex: number;
}) {
  const [rating, setRating] = useState<'up' | 'down'>();
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState('');
  const locked = useRef(false);
  async function vote(value: 'up' | 'down') {
    if (locked.current) return;
    locked.current = true; setPending(true); setStatus('提交中…');
    try {
      await source.feedback({ conversation_id: Number(sessionId), message_id: messageId, user_id: userId, rating: value });
      setRating(value); setStatus(value === 'down' ? '已反馈，我们会改进' : '已反馈');
      try {
        const stored: unknown = JSON.parse(localStorage.getItem('aftersales_feedback') || '[]');
        const records = Array.isArray(stored) ? stored : [];
        localStorage.setItem('aftersales_feedback', JSON.stringify([...records, {
          session_id: sessionId, answer_index: answerIndex, rating: value, at: new Date().toISOString(),
        }]));
      } catch { /* The server already accepted the feedback. */ }
    } catch { setStatus('反馈失败'); locked.current = false; }
    finally { setPending(false); }
  }
  return <div className={styles.feedback}>
    {(['up', 'down'] as const).map(value => <button key={value} type="button" aria-label={value === 'up' ? '有帮助' : '没有帮助'}
      aria-pressed={rating === value} disabled={pending || !!rating} onClick={() => void vote(value)}>{value === 'up' ? '👍' : '👎'}</button>)}
    <span role="status">{status}</span>
  </div>;
}
