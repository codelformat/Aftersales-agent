import { Fragment, useCallback, useId, useRef, useState } from 'react';
import type { DataSource, FaithCase, FaithStatus, ResolveFaithRequest } from '../data/DataSource';
import { dataSource } from '../data';
import { errorMessage, formatDate, LoadNotice, Modal, ReadOnlyNotice, StatusBadge, StatusFilters, text, useListResource } from './shared';
import styles from './Ops.module.css';

function Details({ item }: { item: FaithCase }) {
  return <>
    <h3>答案原文</h3><p className={styles.prose}>{item.answer || '—'}</p>
    <h3>裁判理由</h3><p className={styles.prose}>{item.reason || '—'}</p>
    <h3>证据全集</h3>
    {item.citations?.length ? item.citations.map((evidence, index) => {
      const cited = typeof evidence.n === 'number' && item.cited.includes(evidence.n);
      return <article key={index} className={styles.card} data-cited={cited}>
        <h3>[{text(evidence.n)}] {text(evidence.section_path) || '知识库原文'}</h3>
        {cited && <span className={styles.badge}>已引用</span>}
        <p className={styles.prose}>问：{text(evidence.question)}{'\n'}答：{text(evidence.answer)}</p>
      </article>;
    }) : <p>暂无证据</p>}
    {(item.resolution || item.resolved_at) && <>
      <h3>处置记录</h3><p className={styles.prose}>{item.resolution || '—'}</p>
      {item.resolved_at && <p>处置时间：{formatDate(item.resolved_at)}</p>}
    </>}
  </>;
}
export default function FaithPage({ source = dataSource }: { source?: DataSource }) {
  const [status, setStatus] = useState<FaithStatus | undefined>();
  const loader = useCallback(() => source.faithCases(status), [source, status]);
  const resource = useListResource(loader, '个案列表格式错误');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [pending, setPending] = useState<{ item: FaithCase; status: ResolveFaithRequest['status'] } | null>(null);
  const [resolution, setResolution] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const id = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const replay = source.mode === 'replay';
  const count = Array.from(resolution.trim()).length;
  function toggle(itemId: number) {
    setExpanded(current => { const next = new Set(current); if (next.has(itemId)) next.delete(itemId); else next.add(itemId); return next; });
  }
  function openResolve(item: FaithCase, target: ResolveFaithRequest['status']) {
    if (replay || saving) return;
    setPending({ item, status: target }); setResolution(''); setError('');
  }
  async function resolve() {
    if (!pending || saving || replay) return;
    if (count < 1 || count > 300) { setError('请填写 1–300 字的处置说明'); return; }
    setSaving(true); setError(''); setNotice('');
    try {
      const updated = await source.resolveFaithCase(pending.item.id, { status: pending.status, resolution: resolution.trim() });
      setPending(null); await resource.reload(); setNotice(`已标记为${updated.status}`);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setSaving(false); }
  }
  return <section className={styles.page}>
    <h1 ref={headingRef} tabIndex={-1}>编造台账</h1><p>展开问题，查看答案、裁判理由和证据。</p><ReadOnlyNotice replay={replay} />
    <div className={styles.toolbar}>
      <StatusFilters statuses={['未解决', '已解决', '无需解决'] as const} value={status} onChange={value => { setStatus(value); setNotice(''); }} />
      <button onClick={() => void resource.reload()} disabled={resource.loading}>重新加载</button>
      <span role="status">{notice && `${notice} · `}{resource.data && `共 ${resource.data.length} 个案`}</span>
    </div>
    <LoadNotice {...resource} empty={resource.data?.length === 0} emptyText="当前筛选下暂无个案" />
    <div className={styles.tableWrap} aria-busy={resource.loading}>
      <table className={styles.table} aria-label="编造个案列表">
        <thead><tr>{['题号', '桶', '问题', '出现次数', '最后出现时间', '状态', '处置'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{resource.data?.map(item => <Fragment key={item.id}>
          <tr className={styles.row} onClick={() => toggle(item.id)}>
            <td>{item.eval_id}</td><td>{item.bucket}</td><td><button className={styles.expand} aria-expanded={expanded.has(item.id)} aria-controls={`${id}-${item.id}`}>{item.query}</button></td>
            <td>{item.seen_count}</td><td>{formatDate(item.last_seen_at)}</td>
            <td><StatusBadge status={item.status} />{item.status === '未解决' && item.resolved_at && <> · <span className={styles.error}>复发</span></>}</td>
            <td onClick={event => event.stopPropagation()}><div className={styles.actions}>
              {(['已解决', '无需解决'] as const).map(target => <button key={target} disabled={saving || replay} title={replay ? '本地运行可操作' : undefined}
                aria-label={`将题号 ${item.eval_id} 标记为${target}`} onClick={() => openResolve(item, target)}>{target}</button>)}
            </div></td>
          </tr>
          {expanded.has(item.id) && <tr id={`${id}-${item.id}`}><td colSpan={7} className={styles.detail}><Details item={item} /></td></tr>}
        </Fragment>)}</tbody>
      </table>
    </div>
    {pending && <Modal title={`标记为${pending.status}`} description={`题号：${pending.item.eval_id}`} busy={saving} fallbackFocus={headingRef} onClose={() => setPending(null)}>
      <form noValidate onSubmit={event => { event.preventDefault(); void resolve(); }}>
        <label htmlFor={`${id}-resolution`}>处置说明</label>
        <textarea id={`${id}-resolution`} value={resolution} disabled={saving} required aria-invalid={!!error} aria-describedby={`${id}-hint ${id}-count ${id}-error`}
          onChange={event => { setResolution(event.target.value); setError(''); }} />
        <p id={`${id}-hint`}>必填，去除首尾空白后 1–300 字</p>
        <p id={`${id}-count`} aria-live="polite" className={count > 300 ? styles.error : undefined}>{count} / 300</p>
        <div id={`${id}-error`}>{error && <p role="alert" className={styles.error}>{error}</p>}</div>
        <div className={styles.actions}>
          <button type="button" disabled={saving} onClick={() => setPending(null)}>取消</button>
          <button type="submit" disabled={saving || replay}>{saving ? '提交中…' : '确认提交'}</button>
        </div>
      </form>
    </Modal>}
  </section>;
}
