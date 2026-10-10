import * as Dialog from '@radix-ui/react-dialog';
import * as Tooltip from '@radix-ui/react-tooltip';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { DataSource, KnowledgeChunk } from '../../data/DataSource';
import type { Citation } from '../../protocol/events';
import styles from '../Desk.module.css';

export type CitationHover = (turnId: string, chunkId: number | null) => void;

function CitationMarker({ citation, source, turnId, onCitationHover }: {
  citation: Citation; source: DataSource; turnId: string; onCitationHover?: CitationHover;
}) {
  const [open, setOpen] = useState(false);
  const [chunkId, setChunkId] = useState(citation.chunk_id);
  const [chunk, setChunk] = useState<KnowledgeChunk>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const hovering = useRef(false);
  const hoverCallback = useRef(onCitationHover);
  hoverCallback.current = onCitationHover;
  useEffect(() => () => {
    if (hovering.current) hoverCallback.current?.(turnId, null);
  }, [turnId]);
  useEffect(() => {
    if (!open) return;
    let ignore = false;
    setLoading(true); setError(''); setChunk(undefined);
    void source.knowledgeChunk(chunkId).then(result => {
      if (!ignore) setChunk(result);
    }).catch(() => {
      if (!ignore) setError('原文加载失败');
    }).finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [open, chunkId, source, retry]);

  return <Dialog.Root open={open} onOpenChange={value => { setOpen(value); if (value) setChunkId(citation.chunk_id); }}>
    <Tooltip.Root onOpenChange={value => {
      hovering.current = value; hoverCallback.current?.(turnId, value ? citation.chunk_id : null);
    }}>
      <Dialog.Trigger asChild><Tooltip.Trigger asChild>
        <button type="button" className={styles.citation} aria-label={`查看引用 ${citation.n} 的原文`}>[{citation.n}]</button>
      </Tooltip.Trigger></Dialog.Trigger>
      <Tooltip.Portal><Tooltip.Content className={styles.tooltip} sideOffset={8}>
        <strong>{citation.section_path}</strong><div>问：{citation.question}</div><div>答：{citation.answer}</div>
        <Tooltip.Arrow className={styles.tooltipArrow} />
      </Tooltip.Content></Tooltip.Portal>
    </Tooltip.Root>
    <Dialog.Portal><Dialog.Overlay className={styles.overlay} />
      <Dialog.Content className={styles.dialog}>
        <div className={styles.dialogHeading}><Dialog.Title>{chunk?.section_path || citation.section_path || '知识库原文'}</Dialog.Title>
          <Dialog.Close asChild><button type="button" aria-label="关闭原文卡片">关闭</button></Dialog.Close>
        </div>
        <Dialog.Description>知识库证据原文</Dialog.Description>
        <div className={styles.evidence}>问：{chunk?.questions ?? citation.question}{'\n'}答：{chunk?.answer ?? citation.answer}</div>
        {loading && <p role="status">正在加载原文…</p>}
        {error && <div className={styles.error} role="alert">{error} <button type="button" onClick={() => setRetry(value => value + 1)}>重试加载原文</button></div>}
        <div className={styles.actions}>
          {chunk?.prev_chunk_id != null && <button type="button" disabled={loading} onClick={() => setChunkId(chunk.prev_chunk_id!)}>上一段</button>}
          {chunk?.next_chunk_id != null && <button type="button" disabled={loading} onClick={() => setChunkId(chunk.next_chunk_id!)}>下一段</button>}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

export default function Citations({ text, citations = [], source, turnId, onCitationHover }: {
  text: string; citations?: Citation[]; source: DataSource; turnId: string; onCitationHover?: CitationHover;
}) {
  const items = new Map(citations.map(item => [item.n, item]));
  const parts: ReactNode[] = [];
  let offset = 0;
  for (const match of text.matchAll(/\[(\d+)\]/g)) {
    parts.push(text.slice(offset, match.index));
    const citation = items.get(Number(match[1]));
    parts.push(citation ? <CitationMarker key={`${match.index}-${citation.n}`} citation={citation} source={source}
      turnId={turnId} onCitationHover={onCitationHover} /> : match[0]);
    offset = match.index + match[0].length;
  }
  parts.push(text.slice(offset));
  return <div className={styles.reply}><Tooltip.Provider delayDuration={200}>{parts}</Tooltip.Provider></div>;
}
