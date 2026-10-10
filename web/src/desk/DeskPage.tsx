import { useEffect, useId, useRef, useState } from 'react';
import { useViewMode } from '../app/ViewModeContext';
import { dataSource as defaultSource } from '../data';
import type { DataSource } from '../data/DataSource';
import ChatPane from './ChatPane';
import XrayPanel from '../xray/XrayPanel';
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
  const [xrayCollapsed, setXrayCollapsed] = useState(false);
  const [hoveredCitation, setHoveredCitation] = useState<{ turnId: string; chunkId: number | null }>();
  const toggle = useRef<HTMLButtonElement>(null);
  const sidebarId = useId();
  const selected = selectedTurnId ?? desk.state.activeTurnId;
  useEffect(() => { onSelectedTurnChange?.(selected); }, [selected, onSelectedTurnChange]);
  const sessionId = desk.state.sessionId;
  const displayedTurnId = hoveredCitation?.chunkId != null ? hoveredCitation.turnId : selected;
  useEffect(() => { setSidebarOpen(false); }, [sessionId]);
  function selectTurn(id: string) {
    desk.selectTurn(id);
    if (selectedTurnId !== undefined) onSelectedTurnChange?.(id);
  }
  return <div className={styles.desk} data-view={viewMode} data-sidebar-open={sidebarOpen} data-xray-collapsed={xrayCollapsed} onKeyDown={event => {
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
      onSelect={selectTurn} onCitationHover={(turnId, chunkId) => {
        setHoveredCitation({ turnId, chunkId });
        onCitationHover?.(turnId, chunkId);
      }} onOpenSidebar={() => setSidebarOpen(value => !value)}
      sidebarOpen={sidebarOpen} sidebarId={sidebarId} sidebarToggleRef={toggle} />
    {viewMode === 'eng' && <aside className={styles.perspective} aria-label="透视面板"><XrayPanel turn={desk.state.turns.find(turn => turn.id === displayedTurnId)}
      mode={dataSource.mode} sessionId={sessionId} collapsed={xrayCollapsed} onCollapseChange={setXrayCollapsed}
      highlightedChunkId={hoveredCitation && hoveredCitation.turnId === displayedTurnId ? hoveredCitation.chunkId : null} /></aside>}
  </div>;
}
