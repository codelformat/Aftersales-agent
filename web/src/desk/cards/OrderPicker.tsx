import type { Order } from '../../protocol/events';
import OrderCard from '../OrderCard';
import styles from '../Desk.module.css';

export default function OrderPicker({ orders, disabled, onSelect }: {
  orders: Order[]; disabled: boolean; onSelect: (order: Order) => void;
}) {
  return <section className={styles.card} aria-label="订单选择">
    <h3>请选择要办理的订单</h3>
    <div className={styles.orders}>{orders.map(order => <button key={order.order_id} type="button"
      aria-label={`选择订单 ${order.order_id} ${order.title}`} disabled={disabled} onClick={() => onSelect(order)}>
      <OrderCard order={order} />
    </button>)}</div>
  </section>;
}
