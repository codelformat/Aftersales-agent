import { formatDate } from '../shared';

export interface ChartPoint { id: number; created_at: string; value: number | null }
export default function LineChart({ points, label, declining = false }: { points: ChartPoint[]; label: string; declining?: boolean }) {
  const left = 40, right = 464, top = 16, bottom = 156;
  const times = points.map(point => new Date(point.created_at).getTime());
  const start = times[0] ?? 0, end = times.at(-1) ?? start;
  const x = (index: number) => end === start ? (left + right) / 2 : left + (times[index]! - start) / (end - start) * (right - left);
  const y = (value: number) => bottom - value * (bottom - top);
  let path = '', connected = false;
  for (let index = 0; index < points.length; index++) {
    const value = points[index]!.value;
    if (value === null || !Number.isFinite(value)) { connected = false; continue; }
    path += `${connected ? 'L' : 'M'}${x(index).toFixed(2)},${y(value).toFixed(2)} `;
    connected = true;
  }
  const ticks = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])].filter(index => index >= 0 && index < points.length);
  return <svg viewBox="0 0 504 208" role="img" aria-label={`${label}随时间变化${declining ? '，最新一轮下滑' : ''}`}>
    <title>{label}趋势</title>
    {[0, .5, 1].map(value => <g key={value}>
      <line x1={left} y1={y(value)} x2={right} y2={y(value)} stroke="var(--color-border)" />
      <text x={left - 8} y={y(value) + 4} textAnchor="end">{value.toFixed(1)}</text>
    </g>)}
    {path && <path d={path} fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinejoin="round" />}
    {points.map((point, index) => point.value !== null && Number.isFinite(point.value) && <circle key={point.id} cx={x(index)} cy={y(point.value)} r={3.5} fill="currentColor">
      <title>第 {point.id} 轮 · {formatDate(point.created_at)} · {label} {point.value.toFixed(3)}</title>
    </circle>)}
    {ticks.map(index => <g key={index}>
      <line x1={x(index)} y1={bottom} x2={x(index)} y2={bottom + 5} stroke="var(--color-text-muted)" />
      <text x={x(index)} y={bottom + 24} textAnchor={points.length === 1 ? 'middle' : index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle'}>
        {new Date(points[index]!.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}
      </text>
    </g>)}
    <text x={(left + right) / 2} y={204} textAnchor="middle">时间</text>
  </svg>;
}
