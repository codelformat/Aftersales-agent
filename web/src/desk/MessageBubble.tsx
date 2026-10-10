import type { DataSource } from '../data/DataSource';
import type { Turn } from '../state/conversation';
import type { DeskSession } from './useDeskSession';
import ActionButtons from './cards/ActionButtons';
import Citations from './cards/Citations';
import type { CitationHover } from './cards/Citations';
import Feedback from './cards/Feedback';
import OrderPicker from './cards/OrderPicker';
import TicketPreview from './cards/TicketPreview';
import styles from './Desk.module.css';

const toolNames: Record<string, string> = {
  query_order: '查订单', query_product: '查商品', query_logistics: '查物流', query_faq: '查常见问题',
  create_ticket: '创建工单', offer_human_options: '推荐人工选项', query_warranty: '查保修', query_return_progress: '查退货进度',
};

export default function MessageBubble({ turn, index, desk, source, selected, onSelect, onCitationHover }: {
  turn: Turn; index: number; desk: DeskSession; source: DataSource; selected: boolean;
  onSelect: () => void; onCitationHover?: CitationHover;
}) {
  const latest = desk.state.turns.at(-1)?.id === turn.id;
  const canResume = latest && turn.status === 'interrupted';
  const turnSessionId = desk.turnSessions[turn.id];
  const disabled = desk.busy || source.mode === 'replay' || turnSessionId !== desk.state.sessionId;
  return <div className={styles.turn}>
    {turn.userText && <div className={styles.userRow}><div className={styles.userBubble}>{turn.userText}</div></div>}
    <article className={styles.assistant} aria-label={`客服回复 ${index + 1}`} data-selected={selected} onClick={event => {
      if (!(event.target instanceof Element) || !event.target.closest('button, input, textarea, select, [role="dialog"], [role="tooltip"]')) onSelect();
    }}>
      <div className={styles.replyHeading}><span>售后助手</span>
        <button type="button" className={styles.turnSelect} aria-pressed={selected} aria-label={`查看此轮 ${index + 1}`} onClick={onSelect}>第 {index + 1} 轮</button>
      </div>
      {turn.understood?.resolved_input && turn.understood.resolved_input !== turn.userText &&
        <div className={styles.understood}>已理解为：{turn.understood.resolved_input}{turn.understood.intent ? `（${turn.understood.intent}）` : ''}</div>}
      {!!turn.tools.length && <div className={styles.tools}>{turn.tools.map((tool, i) => {
        const parameter = tool.name === 'create_ticket' ? tool.args.ticket_type : tool.args.order_id ?? tool.args.product_id ?? tool.args.keyword;
        const status = tool.ok === undefined ? '进行中' : tool.ok ? '已完成' : '查询失败';
        return <span key={`${tool.id}-${i}`} data-status={tool.ok === undefined ? 'pending' : tool.ok ? 'success' : 'error'}>
          {tool.ok === undefined ? '○' : tool.ok ? '✓' : '✗'} {toolNames[tool.name] ?? tool.name}
          {parameter != null && parameter !== '' ? ` · ${String(parameter)}` : ''} · {status}
        </span>;
      })}</div>}
      <Citations text={turn.replyText} citations={turn.citations} source={source} turnId={turn.id} onCitationHover={onCitationHover} />
      {turn.status === 'streaming' && <div className={styles.muted} role="status">{turn.tools.some(tool => tool.ok === undefined) ? '正在查询…' : '正在回复…'}</div>}
      {turn.orderPicker && <OrderPicker orders={turn.orderPicker} disabled={disabled || !canResume} onSelect={order => void desk.resumeOrder(turn.id, order)} />}
      {turn.ticketPreview && <TicketPreview preview={turn.ticketPreview} disabled={disabled || !canResume} onConfirm={confirmed => void desk.resumeTicket(turn.id, confirmed)} />}
      {!!turn.actions?.length && <ActionButtons turnId={turn.id} options={turn.actions} desk={desk} disabled={disabled || !desk.state.sessionId} />}
      {desk.notices[turn.id]?.map((notice, i) => <div key={i} className={notice.kind === 'selection' ? styles.selection : styles.notice}>
        <div>{notice.text}</div>{notice.kind === 'handoff' && <><span className={styles.muted}>客服小猫</span><div>您好，我是客服小猫，请问有什么可以帮您的</div></>}
      </div>)}
      {turn.error && <div className={styles.error} role="alert">{turn.error.message || '服务暂时不可用，请稍后重试'}</div>}
      {turn.status === 'done' && turn.messageId !== undefined && turnSessionId && source.mode === 'live' &&
        <Feedback key={turn.messageId} source={source} sessionId={turnSessionId} messageId={turn.messageId} userId={desk.userId} answerIndex={index} />}
    </article>
  </div>;
}
