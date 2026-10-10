import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ViewModeProvider } from '../app/ViewModeContext';
import { ReplayDataSource } from '../data/replay';
import DeskPage from '../desk/DeskPage';
import { FakeDataSource } from '../desk/test-utils';
import RecordedDesk from './RecordedDesk';
import SplitStage from './SplitStage';
import { recording } from './test-fixtures';

describe('recorded chat', () => {
  it.each([false, true])('replaces the live controls with compact session information (engineering=%s)', engineering => {
    const { lines } = recording('multi-turn');
    render(<RecordedDesk lines={lines} index={lines.length - 1} engineering={engineering} source={new ReplayDataSource()} />);
    expect(screen.queryByRole('textbox', { name: '输入您的问题' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '客服工作台' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新对话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '展开会话侧栏' })).not.toBeInTheDocument();
    expect(screen.getByText('会话 #218 · 只读回放')).toBeInTheDocument();
    expect(screen.getAllByRole('article', { name: /客服回复/ })).toHaveLength(4);
  });

  it('also removes the composer and desk title inside the split customer lane', () => {
    const { lines } = recording('flywheel');
    render(<SplitStage lines={lines} index={lines.length - 1} engineering source={new ReplayDataSource()} />);
    const customer = within(screen.getByRole('region', { name: '客户侧' }));
    expect(customer.queryByRole('textbox')).not.toBeInTheDocument();
    expect(customer.queryByRole('heading', { name: '客服工作台' })).not.toBeInTheDocument();
    expect(customer.getByRole('log', { name: '对话消息' })).toContainElement(customer.getByRole('article', { name: '客服回复 2' }));
  });

  it('follows new visible messages and the last remaining message after seeking backwards, even after scrolling up', () => {
    const { lines } = recording('multi-turn');
    const firstDone = lines.findIndex(line => line.event === 'done');
    const secondDone = lines.findIndex((line, index) => index > firstDone && line.event === 'done');
    const source = new ReplayDataSource();
    const desk = (index: number) => <RecordedDesk lines={lines} index={index} engineering={false} source={source} />;
    const view = render(desk(firstDone));
    const log = screen.getByRole('log', { name: '对话消息' });
    // jsdom has no layout: supply only the native scroll metrics, keeping the component real.
    let height = 1000;
    Object.defineProperties(log, {
      scrollHeight: { get: () => height },
      clientHeight: { get: () => 200 },
    });
    log.scrollTop = 0;
    fireEvent.scroll(log);
    height = 1600;
    view.rerender(desk(secondDone));
    expect(within(log).getAllByRole('article', { name: /客服回复/ })).toHaveLength(2);
    expect(log.scrollTop).toBe(1600);

    log.scrollTop = 0;
    fireEvent.scroll(log);
    height = 1000;
    view.rerender(desk(firstDone));
    expect(within(log).getAllByRole('article', { name: /客服回复/ })).toHaveLength(1);
    expect(log.scrollTop).toBe(1000);
  });

  it('keeps the live desk title, new conversation button and enabled composer', async () => {
    render(<ViewModeProvider><DeskPage dataSource={new FakeDataSource()} /></ViewModeProvider>);
    expect(screen.getByRole('heading', { name: '客服工作台' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新对话' })).toBeEnabled();
    expect(await screen.findByRole('textbox', { name: '输入您的问题' })).toBeEnabled();
  });

  it('restores the last visible message when playback rerenders within the same event prefix', () => {
    const { lines } = recording('multi-turn');
    const index = lines.findIndex(line => line.event === 'done');
    const source = new ReplayDataSource();
    const desk = () => <RecordedDesk lines={lines} index={index} engineering={false} source={source} />;
    const view = render(desk());
    const log = screen.getByRole('log', { name: '对话消息' });
    Object.defineProperties(log, {
      scrollHeight: { value: 1000 }, clientHeight: { value: 200 },
    });
    log.scrollTop = 0;
    fireEvent.scroll(log);
    // The player rerenders on timeline position changes even when the inclusive index stays the same.
    view.rerender(desk());
    expect(within(log).getAllByRole('article', { name: /客服回复/ })).toHaveLength(1);
    expect(log.scrollTop).toBe(1000);
  });
});
