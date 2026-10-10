import type { Scene, StrategyComparison } from '../data/DataSource';
const files = {
  ...import.meta.glob<string>('../../public/replays/*.jsonl', { query: '?raw', import: 'default', eager: true }),
  ...import.meta.glob<string>('../../public/snapshots/**/*.json', { query: '?raw', import: 'default', eager: true }),
};
export function fixture(path: string): string | undefined { return files[`../../public${path}`]; }
export function recording(id: string): Scene {
  const [header, ...lines] = fixture(`/replays/${id}.jsonl`)!.trim().split('\n').map(line => JSON.parse(line));
  return { header, lines };
}
export function comparison(): StrategyComparison { return JSON.parse(fixture('/snapshots/strategy-comparison.json')!); }
