import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

export type ViewMode = 'customer' | 'eng';

type ViewModeState = {
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
};

const ViewModeContext = createContext<ViewModeState | undefined>(undefined);

export function ViewModeProvider({ children }: { children: ReactNode }) {
  const [viewMode, setViewMode] = useState<ViewMode>(() =>
    localStorage.getItem('view_mode') === 'customer' ? 'customer' : 'eng',
  );

  useEffect(() => {
    localStorage.setItem('view_mode', viewMode);
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
