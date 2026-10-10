import { useEffect, useRef } from 'react';
import type { RefObject } from 'react';
import type { DataSource } from '../data/DataSource';
import type { DeskSession } from './useDeskSession';
import type { CitationHover } from './cards/Citations';
import Composer from './Composer';
import MessageBubble from './MessageBubble';
import styles from './Desk.module.css';

export default function ChatPane({ desk, source, selectedTurnId, onSelect, onCitationHover, onOpenSidebar, sidebarOpen, sidebarId, sidebarToggleRef }: {
  desk: DeskSession; source: DataSource; selectedTurnId?: string; onSelect: (id: string) => void;
  onCitationHover?: CitationHover; onOpenSidebar: () => void;
  sidebarOpen: boolean; sidebarId: string; sidebarToggleRef: RefObject<HTMLButtonElement | null>;
}) {
  const log = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  useEffect(() => { followBottom.current = true; }, [desk.state.sessionId]);
  useEffect(() => {
    if (followBottom.current && log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [desk.state.turns, desk.notices, desk.busy]);
  return <section className={styles.chat} aria-label="客服对话">
    <header className={styles.header}>
      <button ref={sidebarToggleRef} type="button" className={styles.mobileToggle} aria-label="展开会话侧栏"
        aria-expanded={sidebarOpen} aria-controls={sidebarId} onClick={onOpenSidebar}>会话</button>
      <div><h1>客服工作台</h1><span className={styles.muted}>{desk.state.sessionId ? `会话 #${desk.state.sessionId}` : '新会话'} · 售后服务</span></div>
      <button type="button" disabled={desk.busy} onClick={desk.newSession}>新对话</button>
    </header>
    {desk.historyError && <div role="alert" className={styles.error}>{desk.historyError}</div>}
    {desk.loadingHistory && <div role="status" className={styles.loading}>正在加载会话…</div>}
    <div ref={log} className={styles.messages} role="log" aria-label="对话消息" aria-live="polite" onScroll={() => {
      const node = log.current;
      if (node) followBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 80;
    }}>
      {!desk.state.turns.length && <div className={styles.welcome}>
        <span className={styles.eyebrow}>示例商城 · 售后服务</span>
        <h2>您好，有什么可以帮您？</h2>
        <p>我是示例商城的售后客服助手。您可以咨询订单、物流、退款和售后政策。</p>
      </div>}
      {desk.state.turns.map((turn, index) => <MessageBubble key={turn.id} turn={turn} index={index} desk={desk} source={source}
        selected={selectedTurnId === turn.id} onSelect={() => onSelect(turn.id)} onCitationHover={onCitationHover} />)}
    </div>
    <Composer key={desk.state.sessionId ?? 'new'} disabled={desk.busy || desk.loadingHistory || source.mode === 'replay'} onSend={desk.send} />
  </section>;
}
