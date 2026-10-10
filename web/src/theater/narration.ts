import type { SceneLine } from '../data/DataSource';
import type { NarrationCue } from './scenes';
export type ResolvedCue = { index: number; cue: NarrationCue };
export function resolveCues(lines: SceneLine[], cues: NarrationCue[]): ResolvedCue[] {
  return cues.map(cue => {
    const { event, kind, lane, nth = 1 } = cue.anchor;
    if (!Number.isInteger(nth) || nth < 1) throw new Error(`Invalid narration occurrence: ${event} #${nth}`);
    let matched = 0;
    const index = lines.findIndex(line => {
      const data = line.data;
      const matches = line.event === event && (lane === undefined || line.lane === lane) &&
        (kind === undefined || (typeof data === 'object' && data !== null && 'kind' in data && data.kind === kind));
      return matches && ++matched === nth;
    });
    if (index === -1) throw new Error(`Unresolved narration anchor: ${event}${kind ? `/${kind}` : ''} #${nth} (${lane ?? 'any lane'})`);
    return { index, cue };
  }).sort((a, b) => a.index - b.index);
}
export function currentCue(resolved: ResolvedCue[], index: number): NarrationCue | undefined {
  for (let i = resolved.length - 1; i >= 0; i--) {
    if (resolved[i].index <= index) return resolved[i].cue;
  }
  return undefined;
}
