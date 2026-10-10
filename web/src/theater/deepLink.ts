export interface TheaterLink { scene: string; seconds: number; view: 'eng' | 'cust'; lang?: 'en'; autoplay?: true }
export function parseDeepLink(hash: string): TheaterLink | null {
  const match = /^#\/theater\/([a-z0-9][a-z0-9-]*)(?:\?(.*))?$/i.exec(hash);
  if (!match) return null;
  const params = new URLSearchParams(match[2]);
  const seconds = Number(params.get('t') ?? 0);
  return { scene: match[1], seconds: Number.isFinite(seconds) && seconds >= 0 ? seconds : 0,
    view: params.get('view') === 'cust' ? 'cust' : 'eng',
    ...(params.get('lang') === 'en' ? { lang: 'en' as const } : {}),
    ...(params.get('autoplay') === '1' ? { autoplay: true as const } : {}) };
}
export function formatDeepLink(link: TheaterLink): string {
  const seconds = Number.isFinite(link.seconds) ? Math.max(0, link.seconds) : 0;
  return `#/theater/${encodeURIComponent(link.scene)}?t=${seconds}&view=${link.view}${link.lang === 'en' ? '&lang=en' : ''}${link.autoplay ? '&autoplay=1' : ''}`;
}
