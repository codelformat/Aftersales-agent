import type { ConversationSummary } from '../data/DataSource';
import styles from './Desk.module.css';

export default function SessionList({ conversations, sessionId, disabled, error, onSelect, onRetry }: {
  conversations: ConversationSummary[]; sessionId?: string; disabled: boolean; error: string;
  onSelect: (id: string) => void; onRetry: () => void;
}) {
  return <>
    {error && <div role="alert" className={styles.error}>{error} <button onClick={onRetry} disabled={disabled}>重试会话列表</button></div>}
    <nav aria-label="会话列表" className={styles.sessions}>
      {!conversations.length && !error && <p>暂无历史会话</p>}
      {conversations.map(item => <button key={item.session_id} type="button" disabled={disabled}
        aria-current={sessionId === item.session_id ? 'true' : undefined} onClick={() => onSelect(item.session_id)}>
        <span className={styles.preview}>{item.preview || '新会话'}</span>
        <span className={styles.meta}>
          <time dateTime={item.updated_at}>{Number.isNaN(Date.parse(item.updated_at)) ? '' : new Date(item.updated_at).toLocaleString('zh-CN', {
            month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
          })}</time>
          {item.summarized && <span>已摘要</span>}
        </span>
      </button>)}
    </nav>
  </>;
}
