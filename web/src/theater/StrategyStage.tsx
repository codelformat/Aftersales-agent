import type { ReplayDataSource } from '../data/replay';
import StrategyComparison from '../ops/StrategyComparison';
import styles from './Theater.module.css';
export default function StrategyStage({ source }: { source: ReplayDataSource }) {
  return <div className={styles.strategyStage}><StrategyComparison source={source} /></div>;
}
