import type { SceneLine } from '../src/data/DataSource';
import { ReplayClock } from '../src/data/clock';

// Use the same gap compression as the player, with automatic holds disabled.
export function gateCompletion(lines: SceneLine[], occurrence: number) {
  if (!Number.isInteger(occurrence) || occurrence < 1) throw new RangeError('Gate occurrence must be positive');
  let gates = 0;
  const gateIndex = lines.findIndex(line => line.lane === 'customer' && line.channel === 'sse' &&
    line.event === 'trace' && line.data.kind === 'gate' && ++gates === occurrence);
  if (gateIndex < 0) throw new Error(`Recording has no gate #${occurrence}`);
  const doneIndex = lines.findIndex((line, index) => index > gateIndex && line.lane === 'customer' &&
    line.channel === 'sse' && line.event === 'done');
  if (doneIndex < 0) throw new Error(`Recording has no done after gate #${occurrence}`);
  const turn = lines.slice(0, gateIndex + 1).filter(line => line.lane === 'customer' &&
    line.channel === 'api' && line.event === 'chat').length;
  if (!turn) throw new Error(`Gate #${occurrence} has no customer turn`);
  return { position: new ReplayClock(lines, { autoHold: false }).timeAt(doneIndex), turn };
}
