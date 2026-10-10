import { DATA_SOURCE, REPO_URL } from '../config';
import TopBar from './TopBar';
import { ViewModeProvider } from './ViewModeContext';
import { RoutePage, useHashRoute } from './router';
import styles from './App.module.css';

export default function App() {
  const route = useHashRoute();
  return (
    <ViewModeProvider>
      {DATA_SOURCE === 'replay' && (
        <aside className={styles.replayBanner} aria-label="回放模式说明">
          这是录制的真实会话回放 · <a href={`${REPO_URL}#run-locally`}>在本地运行完整系统 →</a>
        </aside>
      )}
      <TopBar route={route} />
      <main className={styles.content}>
        <RoutePage route={route} />
      </main>
    </ViewModeProvider>
  );
}
