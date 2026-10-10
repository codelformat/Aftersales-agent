import * as ToggleGroup from '@radix-ui/react-toggle-group';
import type { AppRoute } from './router';
import { useViewMode } from './ViewModeContext';
import styles from './App.module.css';

export default function TopBar({ route }: { route: AppRoute }) {
  const { viewMode, setViewMode } = useViewMode();
  return (
    <header className={styles.topBar}>
      <span className={styles.brand}>示例商城客服</span>
      <nav aria-label="主导航" className={styles.navigation}>
        <a href="#/desk" aria-current={route.section === 'desk' ? 'page' : undefined}>工作台</a>
        <a href="#/ops/review" aria-current={route.section === 'ops' ? 'page' : undefined}>运营台</a>
        <a href="#/theater" aria-current={route.section === 'theater' ? 'page' : undefined}>回放剧场</a>
      </nav>
      {route.showViewMode && (
        <ToggleGroup.Root
          className={styles.viewSwitch}
          type="single"
          value={viewMode}
          onValueChange={(value) => {
            if (value === 'customer' || value === 'eng') setViewMode(value);
          }}
          aria-label="视角切换"
        >
          <ToggleGroup.Item value="customer">客户视角</ToggleGroup.Item>
          <ToggleGroup.Item value="eng">工程视角</ToggleGroup.Item>
        </ToggleGroup.Root>
      )}
    </header>
  );
}
