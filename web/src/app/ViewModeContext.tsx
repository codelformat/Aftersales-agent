import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

export type ViewMode = 'customer' | 'eng';

type ViewModeState = {
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
};

const ViewModeContext = createContext<ViewModeState | undefined>(undefined);

export function ViewModeProvider({ children }: { children: ReactNode }) {
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try { return localStorage.getItem('view_mode') === 'customer' ? 'customer' : 'eng'; }
    catch { return 'eng'; }
  });

  useEffect(() => {
    try { localStorage.setItem('view_mode', viewMode); } catch { /* Preserve the selection in memory. */ }
  }, [viewMode]);

  return (
    <ViewModeContext value={{ viewMode, setViewMode }}>
      {children}
    </ViewModeContext>
  );
}

export function useViewMode() {
  const context = useContext(ViewModeContext);
  if (!context) throw new Error('useViewMode requires ViewModeProvider');
  return context;
}
