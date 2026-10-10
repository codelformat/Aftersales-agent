import type { TicketPreview as Preview } from '../../protocol/events';
import styles from '../Desk.module.css';

export default function TicketPreview({ preview, disabled, onConfirm }: {
  preview: Preview; disabled: boolean; onConfirm: (confirmed: boolean) => void;
}) {
  return <section className={styles.card} aria-label="工单预览">
    <h3>请确认工单信息</h3>
    <p>工单类型：{preview.ticket_type}</p><p>问题描述：{preview.description}</p>
    <div className={styles.actions}>
      <button type="button" className={styles.primary} disabled={disabled} onClick={() => onConfirm(true)}>确认提交</button>
      <button type="button" disabled={disabled} onClick={() => onConfirm(false)}>取消</button>
    </div>
  </section>;
}
