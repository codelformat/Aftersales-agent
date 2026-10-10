import { DATA_SOURCE } from '../config';
import { LiveDataSource } from './live';
import { ReplayDataSource } from './replay';
import type { DataSource } from './DataSource';

export const dataSource: DataSource = DATA_SOURCE === 'replay' ? new ReplayDataSource() : new LiveDataSource();
export * from './DataSource';
export { LiveDataSource } from './live';
export { ReplayDataSource, ReplayReadOnlyError, loadScene } from './replay';
export { ReplayClock } from './clock';
export { parseSse } from './sse';
