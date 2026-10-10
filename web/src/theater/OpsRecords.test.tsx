import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { SceneLine } from '../data/DataSource';
import OpsRecords from './OpsRecords';
import { recording } from './test-fixtures';

describe('ops scene focus', () => {
  it('shows the matching review once and collapses unrelated reviews with a unique count', () => {
    const { lines } = recording('flywheel');
    const index = lines.findIndex(line => line.t_ms === 4540);
    render(<OpsRecords lines={lines} index={index} />);
    expect(screen.getAllByRole('heading', { name: 'L2 台灯可以用小爱同学语音控制吗？' })).toHaveLength(1);
    const summary = screen.getByText('另有 1 条待审（展开）');
    const details = summary.closest('details')!;
    expect(details).not.toHaveAttribute('open');
    expect(within(details).getAllByText('X3 Pro 耳机的续航是多久？').length).toBeGreaterThan(0);
    fireEvent.click(summary);
    expect(details).toHaveAttribute('open');
    expect(screen.getByText('L2 参数')).toBeInTheDocument();
  });
  it('marks the matching item approved as soon as the approval response arrives and rewinds to pending', () => {
    const { lines } = recording('flywheel');
    const index = lines.findIndex(line => line.event === 'approve');
    const page = render(<OpsRecords lines={lines} index={index} />);
    const row = screen.getAllByRole('heading', { name: 'L2 台灯可以用小爱同学语音控制吗？' })[0].closest('article')!;
    expect(within(row).getByText('通过')).toBeInTheDocument();
    expect(within(row).queryByText('待审')).not.toBeInTheDocument();
    expect(screen.getByText(/知识块 #481/)).toBeInTheDocument();
    page.rerender(<OpsRecords lines={lines} index={index - 1} />);
    expect(within(row).getByText('待审')).toBeInTheDocument();
    expect(within(row).queryByText('通过')).not.toBeInTheDocument();
  });
  it('matches keywords in source wording even when the normalized question is generic', () => {
    const lines: SceneLine[] = [
      { t_ms: 0, lane: 'customer', channel: 'api', event: 'chat', data: { message: 'L2 台灯支持语音控制吗？' } },
      { t_ms: 1, lane: 'ops', channel: 'api', event: 'poll_review', data: {
        path: '/api/review-queue?status=待审', response: [
          { id: 1, normalized_question: '智能家居功能咨询', review_status: '待审', sources: [{ raw_question: 'L2 能语音控制吗？' }] },
          { id: 2, normalized_question: '可以用耳机听歌吗？', review_status: '待审' },
        ],
      } },
    ];
    render(<OpsRecords lines={lines} index={1} />);
    expect(screen.getByText('另有 1 条待审（展开）')).toBeInTheDocument();
    const matching = screen.getByRole('heading', { name: '智能家居功能咨询' });
    expect(matching.closest('details')).toBeNull();
    expect(screen.getByRole('heading', { name: '可以用耳机听歌吗？', hidden: true }).closest('details')).not.toHaveAttribute('open');
  });
});
