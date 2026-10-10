import { describe, expect, it } from 'vitest';
import { scenes } from './scenes';
import { currentCue, resolveCues } from './narration';
import { strategyScene } from './sceneData';
import type { SceneLine } from '../data/DataSource';
import { comparison, recording } from './test-fixtures';

describe('event-anchored narration', () => {
  it('resolves all bilingual cues in six real recordings and the strategy snapshot', () => {
    expect(scenes).toHaveLength(7);
    for (const scene of scenes) {
      const data = scene.id === 'strategies' ? strategyScene(comparison()) : recording(scene.id);
      expect(scene.narration.length).toBeGreaterThanOrEqual(4);
      expect(scene.narration.length).toBeLessThanOrEqual(7);
      const resolved = resolveCues(data.lines, scene.narration);
      expect(resolved).toHaveLength(scene.narration.length);
      expect(resolved.map(item => item.index)).toEqual([...resolved.map(item => item.index)].sort((a, b) => a - b));
      for (const item of resolved) {
        expect(item.cue.zh.trim()).not.toBe('');
        expect(item.cue.en.trim()).not.toBe('');
      }
    }
  });
  it('matches kind, lane and one-based occurrence, and rejects missing anchors', () => {
    const lines: SceneLine[] = [
      { t_ms: 0, channel: 'api', lane: 'ops', event: 'check', data: { kind: 'gate' } },
      { t_ms: 1, channel: 'api', lane: 'customer', event: 'check', data: { kind: 'gate' } },
      { t_ms: 2, channel: 'api', lane: 'ops', event: 'check', data: { kind: 'gate' } },
    ];
    const cue = { anchor: { event: 'check', kind: 'gate', lane: 'ops', nth: 2 }, zh: '闸', en: 'Gate' };
    expect(resolveCues(lines, [cue])).toEqual([{ index: 2, cue }]);
    expect(currentCue([{ index: 2, cue }], 1)).toBeUndefined();
    expect(currentCue([{ index: 2, cue }], 2)).toBe(cue);
    expect(() => resolveCues(lines, [{ ...cue, anchor: { event: 'missing' } }])).toThrow(/missing/);
    expect(() => resolveCues(lines, [{ ...cue, anchor: { event: 'check', nth: 4 } }])).toThrow();
  });
  it('anchors flywheel review to the new question with its retrieval snapshot', () => {
    const scene = scenes.find(s => s.id === 'flywheel')!;
    const data = recording('flywheel');
    const review = resolveCues(data.lines, scene.narration).find(r => r.cue.anchor.event === 'poll_review')!;
    expect(review.index).toBe(33);
    expect(data.lines[review.index].data).toMatchObject({ response: { id: 6, sources: [{ reason: '检索证据置信度低于门槛' }] } });
  });
});
