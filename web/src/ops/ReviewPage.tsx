import { Fragment, useCallback, useEffect, useId, useRef, useState } from 'react';
import { ApiError } from '../data/DataSource';
import type { DataSource, ReviewDetail, ReviewItem, ReviewStatus } from '../data/DataSource';
import { dataSource } from '../data';
import { errorMessage, formatDate, LoadNotice, Modal, ReadOnlyNotice, StatusBadge, StatusFilters, text, useListResource } from './shared';
import styles from './Ops.module.css';

const categories = ['蓝牙耳机', '羊毛衫', '扫地机器人', '电动牙刷', '台灯', '保温杯', '运动鞋', '手机壳', '通用'];
const sourceNames: Record<string, string> = { retrieval_low_conf: '检索证据低', self_check: '自评不足', user_feedback: '用户反馈' };
function Sources({ detail }: { detail: ReviewDetail }) {
  return <>
    {detail.approved_answer && <><h3>核准答案</h3><p className={styles.prose}>{detail.approved_answer}</p></>}
    {!detail.sources.length && <p>暂无用户原话</p>}
    {detail.sources.map(source => <article key={source.id} className={styles.card}>
      <p>{formatDate(source.created_at)} · <span>{sourceNames[source.source] ?? source.source}</span></p>
      <h3>用户原话</h3><p className={styles.prose}>{source.raw_question}</p>
      {source.reason && <p className={styles.prose}>原因：{source.reason}</p>}
      <h3>召回片段</h3>
      {source.retrieved_chunks?.length ? source.retrieved_chunks.map((chunk, index) => <div key={index} className={styles.card}>
        <h3>{text(chunk.section_path) || '知识库原文'} · 分数 {typeof chunk.score === 'number' && Number.isFinite(chunk.score) ? chunk.score.toFixed(2) : '—'}</h3>
        <p className={styles.prose}>问：{text(chunk.question ?? chunk.questions)}{'\n'}答：{text(chunk.answer)}</p>
      </div>) : <p>该轮没有走检索</p>}
    </article>)}
  </>;
}
function ReviewRow({ item, source, busy, onApprove, onReject }: {
  item: ReviewItem; source: DataSource; busy: boolean; onApprove: () => void; onReject: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const id = useId();
  useEffect(() => {
    if (!open || detail) return;
    let ignore = false;
    setLoading(true); setError('');
    source.review.detail(item.id).then(value => { if (!ignore) setDetail(value); }, reason => {
      if (!ignore) setError(`${errorMessage(reason)}（收起后展开可重试）`);
    }).finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [open, detail, item.id, source]);
  const disabled = busy || source.mode === 'replay';
  return <Fragment>
    <tr className={styles.row} onClick={() => setOpen(!open)}>
      <td><button className={styles.expand} aria-expanded={open} aria-controls={id}>{item.normalized_question}</button></td>
      <td>{item.occurrence_count}</td><td className={styles.prose}>{item.ai_suggested_answer || '—'}</td>
      <td><StatusBadge status={item.review_status} /></td>
      <td onClick={event => event.stopPropagation()}>{item.review_status === '待审' ? <div className={styles.actions}>
        <button disabled={disabled} title={source.mode === 'replay' ? '本地运行可操作' : undefined} onClick={onApprove}>通过</button>
        <button disabled={disabled} title={source.mode === 'replay' ? '本地运行可操作' : undefined} onClick={onReject}>驳回</button>
      </div> : '—'}</td>
    </tr>
    {open && <tr id={id}><td colSpan={5} className={styles.detail} aria-busy={loading}>
      <LoadNotice loading={loading} error={error} empty={false} emptyText="" />
      {detail && <Sources detail={detail} />}
    </td></tr>}
  </Fragment>;
}
export default function ReviewPage({ source = dataSource }: { source?: DataSource }) {
  const [status, setStatus] = useState<ReviewStatus | undefined>('待审');
  const loader = useCallback(() => source.review.list(status), [source, status]);
  const resource = useListResource(loader, '问题列表格式错误');
  const [pending, setPending] = useState<ReviewItem | null>(null);
  const [answer, setAnswer] = useState('');
  const [category, setCategory] = useState('通用');
  const [saving, setSaving] = useState(false);
  const [terminal, setTerminal] = useState(false);
  const [formError, setFormError] = useState('');
  const [notice, setNotice] = useState('');
  const [writeError, setWriteError] = useState('');
  const id = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const replay = source.mode === 'replay';
  function openApprove(item: ReviewItem) {
    if (replay || saving) return;
    setPending(item); setAnswer((item.ai_suggested_answer ?? '').replace(/^\s*（待核实）\s*/, ''));
    setCategory('通用'); setFormError(''); setTerminal(false);
  }
  async function approve() {
    if (!pending || saving || terminal || replay) return;
    if (!answer.trim()) { setFormError('请填写核准答案'); return; }
    setSaving(true); setFormError(''); setWriteError(''); setNotice('');
    try {
      await source.review.approve(pending.id, { approved_answer: answer.trim(), product_category: category });
      setPending(null); await resource.reload(); setNotice('已通过并入库');
    } catch (error) {
      setFormError(errorMessage(error));
      if (error instanceof ApiError && (error.status === 409 || error.status === 502)) {
        setTerminal(true); await resource.reload();
      }
    } finally { setSaving(false); }
  }
  async function reject(item: ReviewItem) {
    if (saving || replay) return;
    setSaving(true); setWriteError(''); setNotice('');
    try {
      await source.review.reject(item.id); await resource.reload(); setNotice('已驳回');
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) await resource.reload();
      setWriteError(errorMessage(error));
    } finally { setSaving(false); }
  }
  return <section className={styles.page}>
    <h1 ref={headingRef} tabIndex={-1}>待审队列</h1><p>展开问题，查看用户原话及该轮检索快照。</p>
    <ReadOnlyNotice replay={replay} />
    <div className={styles.toolbar}>
      <StatusFilters statuses={['待审', '通过', '驳回'] as const} value={status} onChange={value => { setStatus(value); setNotice(''); setWriteError(''); }} />
      <button onClick={() => { setNotice(''); setWriteError(''); void resource.reload(); }} disabled={resource.loading}>刷新</button>
      <span role="status">{notice && `${notice} · `}{resource.data && `共 ${resource.data.length} 个问题`}</span>
    </div>
    {writeError && <p role="alert" className={styles.error}>{writeError}</p>}
    <LoadNotice {...resource} empty={resource.data?.length === 0} emptyText="当前筛选下暂无问题" />
    <div className={styles.tableWrap} aria-busy={resource.loading}>
      <table className={styles.table} aria-label="待审问题列表">
        <thead><tr>{['标准化问题', '出现次数', '示例答案', '状态', '处置'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{resource.data?.map(item => <ReviewRow key={item.id} item={item} source={source} busy={saving}
          onApprove={() => openApprove(item)} onReject={() => void reject(item)} />)}</tbody>
      </table>
    </div>
    {pending && <Modal title="通过并入库" description={pending.normalized_question} busy={saving} fallbackFocus={headingRef} onClose={() => setPending(null)}>
      <form noValidate onSubmit={event => { event.preventDefault(); void approve(); }}>
        <label htmlFor={`${id}-answer`}>核准答案</label>
        <textarea id={`${id}-answer`} required value={answer} disabled={saving} aria-describedby={`${id}-error`} onChange={event => setAnswer(event.target.value)} />
        <label htmlFor={`${id}-category`}>品类</label>
        <select id={`${id}-category`} value={category} disabled={saving} onChange={event => setCategory(event.target.value)}>{categories.map(value => <option key={value}>{value}</option>)}</select>
        <div id={`${id}-error`}>{formError && <p role="alert" className={styles.error}>{formError}</p>}</div>
        <div className={styles.actions}>
          <button type="button" disabled={saving} onClick={() => setPending(null)}>取消</button>
          <button type="submit" disabled={saving || terminal || replay}>{saving ? '提交中…' : '确认通过'}</button>
        </div>
      </form>
    </Modal>}
  </section>;
}
