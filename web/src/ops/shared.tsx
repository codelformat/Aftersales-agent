import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode, RefObject } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { ApiError } from '../data/DataSource';
import styles from './Ops.module.css';

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) return '输入不合法，请检查表单内容';
  if (error instanceof TypeError) return '网络连接失败，请检查网络后重试';
  return error instanceof Error ? error.message : '请求失败，请重新加载';
}
export function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
export function text(value: unknown): string { return value === null || value === undefined ? '' : String(value); }

// Sequence guards prevent a slower filter/refresh response from replacing newer data.
export function useResource<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const sequence = useRef(0);
  const currentLoader = useRef(loader);
  const reload = useCallback(async () => {
    const request = ++sequence.current;
    setLoading(true); setData(null); setError('');
    try {
      const result = await currentLoader.current();
      if (request === sequence.current) setData(result);
    } catch (reason) {
      if (request === sequence.current) setError(errorMessage(reason));
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    currentLoader.current = loader;
    void reload();
    return () => { sequence.current++; };
  }, [loader, reload]);
  return { data, error, loading, reload };
}
export function useListResource<T>(loader: () => Promise<T[]>, formatError: string) {
  const checkedLoader = useCallback(async () => {
    const result = await loader();
    if (!Array.isArray(result)) throw new Error(formatError);
    return result;
  }, [loader, formatError]);
  return useResource(checkedLoader);
}
export function LoadNotice({ loading, error, empty, emptyText }: {
  loading: boolean; error: string; empty: boolean; emptyText: string;
}) {
  if (loading) return <p role="status">正在加载…</p>;
  if (error) return <p role="alert" className={styles.error}>{error}</p>;
  return empty ? <p role="status">{emptyText}</p> : null;
}
export function StatusFilters<T extends string>({ statuses, value, onChange }: {
  statuses: readonly T[]; value: T | undefined; onChange: (status: T | undefined) => void;
}) {
  return <div role="group" aria-label="按状态筛选" className={styles.toolbar}>
    {[undefined, ...statuses].map(status => <button key={status ?? 'all'} type="button" aria-pressed={status === value}
      onClick={() => onChange(status)}>{status ?? '全部'}</button>)}
  </div>;
}
export function StatusBadge({ status }: { status: string }) {
  const tone = ['通过', '已解决', '成功'].includes(status) ? 'success' :
    ['失败', '超时'].includes(status) ? 'error' : ['待审', '未解决', '校验拦下', '权限拒绝'].includes(status) ? 'warning' : 'neutral';
  return <span className={styles.badge} data-tone={tone}>{status}</span>;
}
export function ReadOnlyNotice({ replay }: { replay: boolean }) {
  return replay ? <p className={styles.readOnly} role="status">本地运行可操作</p> : null;
}
export function Modal({ title, description, busy, onClose, children, fallbackFocus }: {
  title: string; description: string; busy: boolean; onClose: () => void; children: ReactNode;
  fallbackFocus: RefObject<HTMLElement | null>;
}) {
  const opener = useRef(document.activeElement);
  return <Dialog.Root open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className={styles.overlay} />
      <Dialog.Content className={styles.modal} onEscapeKeyDown={event => { if (busy) event.preventDefault(); }}
        onPointerDownOutside={event => { if (busy) event.preventDefault(); }}
        onCloseAutoFocus={event => {
          event.preventDefault();
          if (opener.current instanceof HTMLElement && opener.current.isConnected) opener.current.focus();
          else fallbackFocus.current?.focus();
        }}>
        <Dialog.Title>{title}</Dialog.Title>
        <Dialog.Description>{description}</Dialog.Description>
        {children}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
