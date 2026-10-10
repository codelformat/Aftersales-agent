import { describe, expect, it } from 'vitest';
import { parseDeepLink, formatDeepLink } from './deepLink';
describe('theater deep links', () => {
  it.each(['eng', 'cust'] as const)('round trips fractional seconds and %s view', view => {
    const link = { scene: 'mcp-timeout', seconds: 12.375, view };
    expect(parseDeepLink(formatDeepLink(link))).toEqual(link);
    expect(formatDeepLink(link)).toBe(`#/theater/mcp-timeout?t=12.375&view=${view}`);
  });
  it('parses and formats lang and autoplay only when set', () => {
    expect(parseDeepLink('#/theater/flywheel?view=eng&lang=en&autoplay=1'))
      .toEqual({ scene: 'flywheel', seconds: 0, view: 'eng', lang: 'en', autoplay: true });
    expect(Object.keys(parseDeepLink('#/theater/flywheel?lang=fr&autoplay=yes')!)).toEqual(['scene', 'seconds', 'view']);
    expect(formatDeepLink({ scene: 'flywheel', seconds: 0, view: 'eng', lang: 'en', autoplay: true }))
      .toBe('#/theater/flywheel?t=0&view=eng&lang=en&autoplay=1');
  });
  it('defaults to time zero and engineering view, and normalizes invalid time', () => {
    expect(parseDeepLink('#/theater/refund')).toEqual({ scene: 'refund', seconds: 0, view: 'eng' });
    for (const time of ['-1', 'NaN', 'Infinity', 'oops']) {
      expect(parseDeepLink(`#/theater/refund?t=${time}&view=nope`)).toEqual({ scene: 'refund', seconds: 0, view: 'eng' });
    }
    expect(parseDeepLink('#/theater')).toBeNull();
    expect(parseDeepLink('#/desk')).toBeNull();
  });
});
