import { expect, it } from 'vitest';
import { textDiff } from './textDiff';
it.each([
  ['退款', '退款', [{ text: '退款', added: false }]],
  ['能退吗', '空气净化器能退吗', [{ text: '空气净化器', added: true }, { text: '能退吗', added: false }]],
  ['甲乙', '丙丁', [{ text: '丙丁', added: true }]],
  ['它能退吗', '空气净化器能退吗', [{ text: '空气净化器', added: true }, { text: '能退吗', added: false }]],
  ['', '', []], ['退款', '', []], ['', '📦退款', [{ text: '📦退款', added: true }]],
  ['aab', 'ab', [{ text: 'ab', added: false }]],
])('diffs %s → %s using character LCS', (a, b, expected) => { expect(textDiff(a, b)).toEqual(expected); });
