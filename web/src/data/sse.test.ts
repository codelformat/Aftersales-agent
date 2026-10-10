// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { parseSse } from './sse';
import { collectAsync } from './test-utils';

function streamOf(text: string, size = 1) {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream<Uint8Array>({ start(controller) {
    for (let i = 0; i < bytes.length; i += size) controller.enqueue(bytes.slice(i, i + size));
    controller.close();
  } });
}
async function collect(text: string, size = 1) {
  return collectAsync(parseSse(streamOf(text, size)));
}

describe('parseSse', () => {
  it('parses events split into individual bytes, including Chinese UTF-8', async () => {
    expect(await collect('event: token\ndata: {"text":"退款您好😀"}\n\nevent: done\ndata: {"finish_reason":"stop"}\n\n'))
      .toEqual([{ event: 'token', data: '{"text":"退款您好😀"}' }, { event: 'done', data: '{"finish_reason":"stop"}' }]);
  });
  it('handles CRLF split at chunk boundaries and joins multiple data lines', async () => {
    expect(await collect(': keepalive\r\nevent: token\r\ndata: first\r\ndata:  second\r\n: comment\r\ndata:\r\n\r\n'))
      .toEqual([{ event: 'token', data: 'first\n second\n' }]);
  });
  it('ignores comments and unknown fields and resets the default event name', async () => {
    expect(await collect('event: custom\nid: 7\nretry: 50\ndata: one\n\n: ping\n\ndata: two\n\nevent:\ndata: three\n\n', 20))
      .toEqual([{ event: 'custom', data: 'one' }, { event: 'message', data: 'two' }, { event: 'message', data: 'three' }]);
  });
  it('supports bare CR terminators and does not dispatch incomplete events at EOF', async () => {
    expect(await collect('data: yes\r\rdata: unfinished\n')).toEqual([{ event: 'message', data: 'yes' }]);
  });
  it('dispatches empty data but skips blocks without data', async () => {
    expect(await collect('event: skipped\n\ndata\n\n')).toEqual([{ event: 'message', data: '' }]);
  });
  it('cancels and unlocks the reader when the consumer stops early', async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(c) { c.enqueue(new TextEncoder().encode('data: first\n\n')); },
      cancel() { cancelled = true; },
    });
    for await (const _ of parseSse(stream)) { void _; break; }
    expect(cancelled).toBe(true);
    expect(stream.locked).toBe(false);
  });
});
