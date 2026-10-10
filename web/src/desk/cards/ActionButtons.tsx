import * as Dialog from '@radix-ui/react-dialog';
import { useId, useRef, useState } from 'react';
import type { Action } from '../../protocol/events';
import type { DeskSession } from '../useDeskSession';
import { actionKey } from '../useDeskSession';
import RefundForm from './RefundForm';
import styles from '../Desk.module.css';

export default function ActionButtons({ turnId, options, desk, disabled }: {
  turnId: string; options: Action[]; desk: DeskSession; disabled: boolean;
}) {
  const [action, setAction] = useState<Action>();
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const submitting = useRef(false);
  const opener = useRef<HTMLButtonElement | null>(null);
  const id = useId();
  async function submit(perform: () => Promise<void>) {
    if (submitting.current) return;
    submitting.current = true; setPending(true); setError('');
    try { await perform(); setAction(undefined); }
    catch (error) { setError(error instanceof Error ? error.message : '提交失败，请稍后重试'); }
    finally { submitting.current = false; setPending(false); }
  }
  const busy = pending || desk.busy;
  const title = action?.type === 'refund' ? '提交退款单' : action?.type === 'ticket' ? '确认创建工单' : '确认转接人工客服吗？';
  return <Dialog.Root open={!!action} onOpenChange={open => { if (!open && !submitting.current) setAction(undefined); }}>
    <div className={styles.actions}>{options.map(option => {
      const complete = desk.completed[turnId]?.includes(actionKey(option));
      return <button key={actionKey(option)} type="button" disabled={disabled || complete} onClick={event => {
        opener.current = event.currentTarget; setDescription(option.type === 'ticket' ? option.description.slice(0, 500) : '');
        setError(''); setAction(option);
      }}>{option.type === 'refund' ? complete ? '已提交' : '提交退款单' : option.type === 'ticket' ? '建工单' : '转人工'}</button>;
    })}</div>
    <Dialog.Portal><Dialog.Overlay className={styles.overlay} />
      <Dialog.Content className={styles.dialog} onEscapeKeyDown={event => { if (submitting.current) event.preventDefault(); }}
        onPointerDownOutside={event => { if (submitting.current) event.preventDefault(); }}
        onCloseAutoFocus={event => { event.preventDefault(); opener.current?.focus(); }}>
        <Dialog.Title>{title}</Dialog.Title><Dialog.Description>请核对信息后确认。</Dialog.Description>
        {action?.type === 'refund' ? <RefundForm orderId={action.order_id} busy={busy} error={error}
          onCancel={() => setAction(undefined)} onSubmit={values => void submit(() => desk.submitRefund(turnId, action, values))} /> :
          <form className={styles.fields} onSubmit={event => {
            event.preventDefault(); if (busy || !action) return;
            void submit(() => action.type === 'ticket' ? desk.submitTicket(turnId, action, description) : desk.handoff(turnId));
          }}>
            {action?.type === 'ticket' && <>
              <p>工单类型：{action.ticket_type}</p><label htmlFor={id}>描述</label>
              <textarea id={id} rows={4} maxLength={500} required value={description} disabled={busy} onChange={event => setDescription(event.target.value)} />
            </>}
            {error && <div className={styles.error} role="alert">{error}</div>}
            <div className={styles.actions}>
              <button type="button" disabled={busy} onClick={() => setAction(undefined)}>取消</button>
              <button type="submit" className={styles.primary} disabled={busy || (action?.type === 'ticket' && !description.trim())}>确认</button>
            </div>
          </form>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
