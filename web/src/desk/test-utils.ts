import { vi } from 'vitest';
import { LiveDataSource } from '../data/live';
import type { ChatRequest, ResumeRequest } from '../data/DataSource';
import type { ChatEvent, Order } from '../protocol/events';

export const order: Order = { order_id: '1001', title: '空气净化器', total: 299, created_at: '2026-10-09', status: '已签收' };
export const session: ChatEvent = { event: 'session', data: { session_id: '42' } };
export const done: ChatEvent = { event: 'done', data: { finish_reason: 'stop', message_id: 81 } };
export const interrupted: ChatEvent = { event: 'done', data: { finish_reason: 'interrupted' } };
export const token = (text: string): ChatEvent => ({ event: 'token', data: { text } });
export async function* sequence(events: ChatEvent[]) { yield* events; }

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

export function eventStream() {
  const queue: ChatEvent[] = [];
  let wake = () => {};
  let closed = false;
  return {
    push(event: ChatEvent) { queue.push(event); wake(); },
    close() { closed = true; wake(); },
    async *[Symbol.asyncIterator]() {
      while (!closed || queue.length) {
        if (queue.length) yield queue.shift()!;
        else await new Promise<void>(resolve => { wake = resolve; });
      }
    },
  };
}

// Fake only the external boundary. Components, hook and reducer stay real.
export class FakeDataSource extends LiveDataSource {
  chat = vi.fn<(_: ChatRequest) => AsyncIterable<ChatEvent>>(() => sequence([session, token('您好'), done]));
  resume = vi.fn<(_: ResumeRequest) => AsyncIterable<ChatEvent>>(() => sequence([token('已处理'), done]));
  conversations = vi.fn<LiveDataSource['conversations']>().mockResolvedValue([]);
  messages = vi.fn<LiveDataSource['messages']>().mockResolvedValue([]);
  feedback = vi.fn<LiveDataSource['feedback']>().mockResolvedValue(undefined);
  submitRefund = vi.fn<LiveDataSource['submitRefund']>().mockResolvedValue({ refund_no: 'RF-9', status: '待审核' });
  submitTicket = vi.fn<LiveDataSource['submitTicket']>().mockResolvedValue({ ticket_no: 'TK-9', status: '待处理' });
  knowledgeChunk = vi.fn<LiveDataSource['knowledgeChunk']>().mockResolvedValue({
    id: 7, section_path: '退款政策', content_type: 'faq', questions: '退款多久到账？', answer: '原文：三个工作日。',
    prev_chunk_id: 6, next_chunk_id: 8,
  });
}
