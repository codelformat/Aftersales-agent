import { describe, expect, it } from 'vitest';
import { replayHead } from '../../replayMeta';

describe('replay metadata', () => {
  it('replaces the title and inserts social preview metadata', () => {
    const html = replayHead('<html><head><title>示例商城客服</title></head><body></body></html>');
    expect(html).toContain('<title>Aftersales Agent · Replay</title>');
    expect(html).not.toContain('示例商城客服');
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const description = 'A LangGraph after-sales agent that cites evidence, refuses when evidence is weak, and learns from human review. Real recorded sessions.';
    for (const [attribute, name, content] of [
      ['name', 'description', description], ['property', 'og:description', description],
      ['property', 'og:type', 'website'], ['property', 'og:title', 'Aftersales Agent · Replay'],
      ['property', 'og:url', 'https://codelformat.github.io/Aftersales-agent/'],
      ['property', 'og:image', 'https://codelformat.github.io/Aftersales-agent/og.png'],
      ['property', 'og:image:width', '1200'], ['property', 'og:image:height', '630'],
      ['name', 'twitter:card', 'summary_large_image'],
    ]) expect(doc.querySelector(`meta[${attribute}="${name}"]`)?.getAttribute('content')).toBe(content);
  });
  it('rejects template title drift', () => {
    expect(() => replayHead('<title>Other title</title>')).toThrow();
  });
});
