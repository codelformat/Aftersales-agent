import { useId, useState } from 'react';
import type { RefundRequest } from '../../data/DataSource';
import styles from '../Desk.module.css';

const reasons: RefundRequest['reason'][] = ['七天无理由', '质量问题', '商品与描述不符', '发错货或漏发', '物流损坏', '其他'];
export default function RefundForm({ orderId, busy, error, onSubmit, onCancel }: {
  orderId: string; busy: boolean; error: string;
  onSubmit: (values: Pick<RefundRequest, 'reason' | 'note'>) => void; onCancel: () => void;
}) {
  const id = useId();
  const [reason, setReason] = useState<RefundRequest['reason'] | ''>('');
  const [note, setNote] = useState('');
  return <form className={styles.fields} onSubmit={event => {
    event.preventDefault(); if (reason && !busy) onSubmit({ reason, note });
  }}>
    <p>订单号：{orderId}</p>
    <label htmlFor={`${id}-reason`}>退款原因</label>
    <select id={`${id}-reason`} value={reason} disabled={busy} required onChange={event => setReason(event.target.value as RefundRequest['reason'])}>
      <option value="">请选择退款原因</option>{reasons.map(value => <option key={value}>{value}</option>)}
    </select>
    <label htmlFor={`${id}-note`}>备注（可选）</label>
    <textarea id={`${id}-note`} value={note} disabled={busy} rows={3} maxLength={200} aria-describedby={`${id}-count`}
      onChange={event => setNote(event.target.value)} />
    <div id={`${id}-count`} className={styles.hint} aria-live="polite">剩余 {200 - note.length} 字</div>
    {error && <div className={styles.error} role="alert">{error}</div>}
    <div className={styles.actions}>
      <button type="button" onClick={onCancel} disabled={busy}>取消</button>
      <button type="submit" className={styles.primary} disabled={busy || !reason}>提交</button>
    </div>
  </form>;
}
