import type { SceneLine } from '../data/DataSource';
import type { ReplayDataSource } from '../data/replay';
import RecordedDesk from './RecordedDesk';
import OpsRecords from './OpsRecords';
import styles from './Theater.module.css';
export default function SplitStage({ lines, index, engineering, source }: {
  lines: SceneLine[]; index: number; engineering: boolean; source: ReplayDataSource;
}) {
  return <div className={styles.split}>
    <section aria-label="客户侧"><h2>客户</h2><RecordedDesk lines={lines} index={index} engineering={engineering} source={source} collapsibleXray /></section>
    <section aria-label="运营侧"><h2>运营 · 只读录制</h2><OpsRecords lines={lines} index={index} /></section>
  </div>;
}
