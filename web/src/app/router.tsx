import { useEffect, useSyncExternalStore } from 'react';
import { DATA_SOURCE } from '../config';
import DeskPage from '../desk/DeskPage';

export type AppRoute = {
  section: 'desk' | 'ops' | 'theater' | 'missing';
  title: string;
  showViewMode: boolean;
  scene?: string;
};

const opsTitles: Record<string, string> = {
  '/ops/review': '待审队列',
  '/ops/evals': '评估趋势',
  '/ops/faith': '编造台账',
  '/ops/tools': '工具审计',
};

function subscribe(onChange: () => void) {
  window.addEventListener('hashchange', onChange);
  return () => window.removeEventListener('hashchange', onChange);
}

function getPath() {
  return window.location.hash.slice(1) || '/';
}

export function useHashRoute(): AppRoute {
  const path = useSyncExternalStore(subscribe, getPath);
  const redirect = path === '/' && DATA_SOURCE === 'replay';

  useEffect(() => {
    if (redirect) window.location.replace('#/theater');
  }, [redirect]);

  if (redirect || path === '/theater') {
    return { section: 'theater', title: '场景库', showViewMode: false };
  }
  if (path === '/' || path === '/desk') {
    return { section: 'desk', title: '客服工作台', showViewMode: true };
  }
  if (Object.hasOwn(opsTitles, path)) {
    return { section: 'ops', title: opsTitles[path]!, showViewMode: false };
  }
  const scene = /^\/theater\/([^/]+)$/.exec(path)?.[1];
  if (scene) {
    return { section: 'theater', title: '场景播放器', showViewMode: true, scene };
  }
  return { section: 'missing', title: '页面未找到', showViewMode: false };
}

export function RoutePage({ route }: { route: AppRoute }) {
  if (route.section === 'desk') return <DeskPage />;
  return (
    <section>
      <h1>{route.title}</h1>
      {route.scene && <p>场景：{route.scene}</p>}
      <p>页面内容待接入。</p>
    </section>
  );
}
