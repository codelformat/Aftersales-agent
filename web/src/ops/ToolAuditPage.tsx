import { useCallback, useId, useState } from 'react';
import type { AuditStatus, DataSource } from '../data/DataSource';
import { dataSource } from '../data';
import { formatDate, LoadNotice, StatusBadge, useListResource } from './shared';
import styles from './Ops.module.css';

const statuses: AuditStatus[] = ['成功', '失败', '超时', '校验拦下', '权限拒绝'];
export default function ToolAuditPage({ source = dataSource }: { source?: DataSource }) {
  const [status, setStatus] = useState<AuditStatus | undefined>();
  const loader = useCallback(() => source.toolAudit(50, status), [source, status]);
  const resource = useListResource(loader, '工具审计列表格式错误');
  const id = useId();
  const rows = [...(resource.data ?? [])].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime() || b.id - a.id);
  return <section className={styles.page}>
    <h1>工具审计</h1><p>按时间倒序显示最近 50 次工具调用。</p>
    <div className={styles.toolbar}>
      <label htmlFor={id}>状态筛选</label>
      <select id={id} value={status ?? ''} onChange={event => setStatus(event.target.value ? event.target.value as AuditStatus : undefined)}>
        <option value="">全部</option>{statuses.map(value => <option key={value}>{value}</option>)}
      </select>
      <button disabled={resource.loading} onClick={() => void resource.reload()}>刷新</button>
      <span role="status">{resource.data && `共 ${resource.data.length} 次调用`}</span>
    </div>
    <LoadNotice {...resource} empty={resource.data?.length === 0} emptyText="当前筛选下暂无工具调用" />
    <div className={styles.tableWrap} aria-busy={resource.loading}>
      <table className={styles.table} aria-label="工具审计列表">
        <thead><tr>{['时间', '工具', '来源', '状态', '重试', '耗时', '错误'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.id}>
          <td>{formatDate(row.created_at)}</td><td className={styles.prose}>{row.tool_name}</td>
          <td>{row.tool_source}{row.mcp_server ? ` · ${row.mcp_server}` : ''}</td>
          <td><StatusBadge status={row.status} /></td><td>{row.retry_count}</td>
          <td>{row.duration_ms === null ? '—' : `${row.duration_ms} ms`}</td><td className={styles.prose}>{row.error_message || '—'}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>;
}
