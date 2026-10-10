import type { OrderSummary } from './useDeskSession';
import styles from './Desk.module.css';

export default function OrderCard({ order }: { order: OrderSummary }) {
  return <div className={styles.orderDetails}>
    {order.title && <strong>{order.title}</strong>}
    <span>订单号：{order.order_id}</span>
    {order.total !== undefined && <span>金额：¥{order.total}</span>}
    {order.created_at && <span>下单时间：{order.created_at}</span>}
    {order.status && <span className={styles.muted}>{order.status}</span>}
  </div>;
}
