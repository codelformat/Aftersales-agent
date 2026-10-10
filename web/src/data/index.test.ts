import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });
it.each(['live', 'replay'])('exports a singleton selected by DATA_SOURCE=%s', async mode => {
  vi.stubEnv('VITE_DATA_SOURCE', mode);
  vi.resetModules();
  const first = await import('./index');
  expect(first.dataSource.mode).toBe(mode);
  expect((await import('./index')).dataSource).toBe(first.dataSource);
});
