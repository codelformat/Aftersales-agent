import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import GateDetail from './details/GateDetail';
import IntentDetail from './details/IntentDetail';
import RetrievalDetail from './details/RetrievalDetail';
import XrayPanel from './XrayPanel';
import { gate, intent, llm, retrieval, turn } from './test-fixtures';
afterEach(() => { vi.unstubAllEnvs(); });
it('renders three weighted SVG segments and a threshold marker with numeric text', () => {
  const { container } = render(<GateDetail data={gate.data} />);
  const segments = container.querySelectorAll('rect[data-signal]');
  expect(segments).toHaveLength(3);
  [40, 18, 4].forEach((width, i) => expect(Number(segments[i].getAttribute('width'))).toBeCloseTo(width));
  expect(Array.from(segments).map(segment => Number(segment.getAttribute('x')))).toEqual([0, 40, 58]);
  expect(Number(container.querySelector('line[data-threshold]')?.getAttribute('x1'))).toBeCloseTo(55);
  expect(screen.getByText(/置信度 0.620.*门槛 0.550/)).toBeInTheDocument();
  expect(screen.getByText(/Top1.*0.500.*0.800.*0.400/)).toBeInTheDocument();
  expect(screen.getByText(/自评通过/)).toBeInTheDocument();
});
it('shows self-check rejection separately from retrieval rejection', () => {
  render(<GateDetail data={{ ...gate.data, passed: false, source: 'self_check', reason: '无法回答' }} />);
  expect(screen.getByText(/自评拦下/)).toBeInTheDocument();
  expect(screen.getByText('无法回答')).toBeInTheDocument();
});
it('shows intent upgrade threshold, route and null confidence honestly', () => {
  const { container, rerender } = render(<IntentDetail data={intent.data} />);
  expect(Number(container.querySelector('line')?.getAttribute('x1'))).toBeCloseTo(70);
  expect(screen.getByText(/aftersales/)).toBeInTheDocument();
  expect(screen.getByText('已升级')).toBeInTheDocument();
  rerender(<IntentDetail data={{ ...intent.data, confidence: null, intent: null }} />);
  expect(screen.getByText('置信度未提供')).toBeInTheDocument();
});
it('limits retrieval to Top-5 and marks below-threshold rows with text', () => {
  render(<RetrievalDetail data={{ ...retrieval.data, top: Array.from({ length: 6 }, (_, i) => ({ ...retrieval.data.top[1], chunk_id: i + 1 })) }} />);
  expect(screen.getAllByRole('listitem')).toHaveLength(5);
  expect(screen.getAllByText('低于门槛')).toHaveLength(5);
});
it('shows an explicit empty-trace message and supports panel collapse', async () => {
  render(<XrayPanel turn={{ ...turn, trace: [] }} mode="live" sessionId="42" />);
  expect(screen.getByText('该轮没有调试数据')).toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: '收起透视面板' }));
  expect(screen.queryByText('该轮没有调试数据')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '展开透视面板' }));
  expect(screen.getByText('该轮没有调试数据')).toBeInTheDocument();
});
it('navigates waterfall rows with arrows and Enter expands details', async () => {
  render(<XrayPanel turn={turn} mode="live" sessionId="42" />);
  const user = userEvent.setup();
  const first = screen.getByRole('button', { name: /指代消解.*resolve_reference/ });
  const second = screen.getByRole('button', { name: /意图识别.*classify_intent/ });
  first.focus(); await user.keyboard('{ArrowDown}'); expect(second).toHaveFocus();
  await user.keyboard('{ArrowUp}'); expect(first).toHaveFocus();
  await user.keyboard('{Enter}'); expect(first).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('空气净化器')).toHaveProperty('tagName', 'MARK');
  expect(screen.getByText('空气净化器退款政策')).toBeInTheDocument();
  expect(screen.getByText('订单范围')).toBeInTheDocument();
  expect(screen.getByText('回顾历史')).toBeInTheDocument();
});
it('automatically reveals and highlights the hovered retrieval chunk and clears on leave', () => {
  const { rerender } = render(<XrayPanel turn={turn} mode="live" highlightedChunkId={7} />);
  expect(screen.getByRole('listitem', { name: /退款政策\/期限/ })).toHaveAttribute('data-highlighted', 'true');
  expect(screen.getByRole('listitem', { name: /保修政策/ })).toHaveAttribute('data-highlighted', 'false');
  rerender(<XrayPanel turn={turn} mode="live" />);
  expect(screen.queryByRole('listitem', { name: /退款政策\/期限/ })).not.toBeInTheDocument();
});
it('shows tools, Agent steps, citations and context budgets in node details', () => {
  render(<XrayPanel turn={turn} mode="live" />);
  fireEvent.click(screen.getByRole('button', { name: /Agent 推理.*agent_model/ }));
  expect(screen.getByText('第 1 步')).toBeInTheDocument();
  expect(screen.getByText(/\[1\].*chunk #7/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /工具执行.*agent_tools/ }));
  expect(screen.getByText('query_logistics')).toBeInTheDocument();
  expect(screen.getByText('MCP · logistics')).toBeInTheDocument();
  expect(screen.getByText(/超时.*重试 2.*40 ms/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /收尾.*finalize/ }));
  expect(screen.getByRole('meter', { name: '层 1 token' })).toHaveAttribute('value', '900');
  expect(screen.getByText('已触发摘要')).toBeInTheDocument();
});
it('aggregates LLM usage with cache and reasoning columns and conditionally links live sessions', () => {
  vi.stubEnv('VITE_LANGFUSE_URL', 'https://langfuse.example/');
  const { rerender } = render(<XrayPanel turn={{ ...turn, trace: [...turn.trace, { ...llm, t_ms: 200 }] }} mode="live" sessionId="42" />);
  const summary = within(screen.getByRole('region', { name: '本轮汇总' }));
  expect(summary.getByText('200 ms')).toBeInTheDocument();
  expect(summary.getByLabelText('LLM 次数')).toHaveTextContent('2');
  expect(summary.getByLabelText('输入 token')).toHaveTextContent('240');
  expect(summary.getByLabelText('输出 token')).toHaveTextContent('100');
  expect(summary.getByLabelText('缓存 token')).toHaveTextContent('160');
  expect(summary.getByLabelText('思考 token')).toHaveTextContent('40');
  expect(summary.getByRole('link', { name: 'Langfuse ↗' })).toHaveAttribute('href', 'https://langfuse.example/project/aftersales/sessions/42');
  rerender(<XrayPanel turn={turn} mode="replay" sessionId="42" />);
  expect(screen.queryByRole('link', { name: 'Langfuse ↗' })).not.toBeInTheDocument();
  vi.stubEnv('VITE_LANGFUSE_URL', ''); rerender(<XrayPanel turn={turn} mode="live" sessionId="42" />);
  expect(screen.queryByRole('link', { name: 'Langfuse ↗' })).not.toBeInTheDocument();
});
it('reveals a hovered citation outside the initial Top-5 retrieval rows', () => {
  render(<RetrievalDetail data={{ ...retrieval.data, top: Array.from({ length: 6 }, (_, i) => ({ ...retrieval.data.top[0], chunk_id: i + 1 })) }} highlightedChunkId={6} />);
  expect(screen.getByRole('listitem', { name: /chunk #6/ })).toHaveAttribute('data-highlighted', 'true');
});
