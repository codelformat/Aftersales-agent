import { useMemo, useRef, useState } from 'react';
import type { SceneLine } from '../data/DataSource';
import type { ReplayDataSource } from '../data/replay';
import ChatPane from '../desk/ChatPane';
import OrderCard from '../desk/OrderCard';
import XrayPanel from '../xray/XrayPanel';
import { buildTimeline } from '../xray/buildTimeline';
import { projectRecording, readOnlyDesk } from './projectRecording';
import styles from './Theater.module.css';
export default function RecordedDesk({ lines, index, engineering, source, collapsibleXray = false }: {
  lines: SceneLine[]; index: number; engineering: boolean; source: ReplayDataSource;
  collapsibleXray?: boolean;
}) {
  const projection = useMemo(() => projectRecording(lines, index), [lines, index]);
  const [selected, setSelected] = useState<string>();
  const [xrayOpen, setXrayOpen] = useState(false);
  const [highlight, setHighlight] = useState<{ id: string; chunk: number | null }>();
  const toggle = useRef<HTMLButtonElement>(null);
  const selectedId = projection.state.turns.some(turn => turn.id === selected) ? selected : projection.state.activeTurnId;
  const desk = readOnlyDesk(projection, setSelected);
  const turn = projection.state.turns.find(turn => turn.id === (highlight?.chunk != null ? highlight.id : selectedId));
  const order = [...projection.state.turns].reverse().flatMap(turn => turn.tools).find(tool => typeof tool.args.order_id === 'string')?.args.order_id;
  return <div className={engineering ? styles.deskEngineering : styles.deskCustomer}>
    <div className={styles.chatStage}>
      {typeof order === 'string' && <OrderCard order={{ order_id: order }} />}
      <ChatPane readOnly desk={desk} source={source} selectedTurnId={selectedId} onSelect={setSelected}
        onCitationHover={(id, chunk) => setHighlight({ id, chunk })} onOpenSidebar={() => {}}
        sidebarOpen={false} sidebarId="recorded-sidebar" sidebarToggleRef={toggle} />
    </div>
    {engineering && collapsibleXray && <button className={styles.xrayToggle} aria-expanded={xrayOpen} aria-controls="split-xray"
      onClick={() => setXrayOpen(open => !open)}>透视面板 · {buildTimeline(turn?.trace ?? []).length} 个节点</button>}
    {engineering && (!collapsibleXray || xrayOpen) && <aside id={collapsibleXray ? 'split-xray' : undefined} aria-label="透视面板" className={styles.xray}>
      <XrayPanel turn={turn} mode="replay" sessionId={projection.state.sessionId} highlightedChunkId={highlight?.chunk ?? null} />
    </aside>}
  </div>;
}
