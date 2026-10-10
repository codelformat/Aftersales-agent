import { useEffect, useId, useRef, useState } from 'react';
import { useViewMode } from '../app/ViewModeContext';
import { dataSource as defaultSource } from '../data';
import type { DataSource } from '../data/DataSource';
import ChatPane from './ChatPane';
import OrderCard from './OrderCard';
import SessionList from './SessionList';
import { useDeskSession } from './useDeskSession';
import styles from './Desk.module.css';
export interface DeskPageProps {
  dataSource?: DataSource;
  onCitationHover?: (turnId: string, chunkId: number | null) => void;
  selectedTurnId?: string;
  onSelectedTurnChange?: (turnId: string | undefined) => void;
}
export default function DeskPage({ dataSource = defaultSource, onCitationHover, selectedTurnId, onSelectedTurnChange }: DeskPageProps) {
  const desk = useDeskSession(dataSource);
  const { viewMode } = useViewMode();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const sidebarId = useId();
  const selected = selectedTurnId ?? desk.state.activeTurnId;
  useEffect(() => { onSelectedTurnChange?.(selected); }, [selected, onSelectedTurnChange]);
  const sessionId = desk.state.sessionId;
  useEffect(() => { setSidebarOpen(false); }, [sessionId]);
  function selectTurn(id: string) {
    desk.selectTurn(id);
    if (selectedTurnId !== undefined) onSelectedTurnChange?.(id);
  }
  return <div className={styles.desk} data-view={viewMode} data-sidebar-open={sidebarOpen} onKeyDown={event => {
    if (event.key === 'Escape' && sidebarOpen) { setSidebarOpen(false); toggle.current?.focus(); }
  }}>
    <aside id={sidebarId} className={styles.sidebar} aria-label="会话侧栏">
      <div className={styles.sidebarHeading}><h2>历史会话</h2>
        <button className={styles.mobileToggle} type="button" aria-label="收起会话侧栏" onClick={() => { setSidebarOpen(false); toggle.current?.focus(); }}>关闭</button>
      </div>
      <SessionList conversations={desk.conversations} sessionId={sessionId} disabled={desk.busy} error={desk.listError}
        onSelect={id => { setSidebarOpen(false); void desk.loadConversation(id); }} onRetry={() => void desk.refreshConversations()} />
      {desk.selectedOrder && <section className={styles.currentOrder} aria-label="当前订单"><h2>当前订单</h2><OrderCard order={desk.selectedOrder} /></section>}
    </aside>
    <ChatPane key={sessionId ?? 'new'} desk={desk} source={dataSource} selectedTurnId={selected}
      onSelect={selectTurn} onCitationHover={onCitationHover} onOpenSidebar={() => setSidebarOpen(value => !value)}
      sidebarOpen={sidebarOpen} sidebarId={sidebarId} sidebarToggleRef={toggle} />
    {viewMode === 'eng' && <aside className={styles.perspective} aria-label="透视面板"><h2>透视面板</h2><p>透视面板将在下一任务接入</p></aside>}
  </div>;
}
