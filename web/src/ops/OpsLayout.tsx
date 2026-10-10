import type { ReactNode } from 'react';
import styles from './Ops.module.css';

export type OpsPage = 'review' | 'evals' | 'faith' | 'tools';
const entries: { page: OpsPage; label: string }[] = [
  { page: 'review', label: '待审队列' }, { page: 'evals', label: '评估' },
  { page: 'faith', label: '编造台账' }, { page: 'tools', label: '工具审计' },
];
export default function OpsLayout({ page, children }: { page: OpsPage; children: ReactNode }) {
  return <div className={styles.layout}>
    <nav aria-label="运营台子导航" className={styles.nav}>
      {entries.map(entry => <a key={entry.page} href={`#/ops/${entry.page}`} aria-current={entry.page === page ? 'page' : undefined}>{entry.label}</a>)}
    </nav>
    {children}
  </div>;
}
