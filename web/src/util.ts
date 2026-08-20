const nf = new Intl.NumberFormat("en-US");

export const fmt = (n: number): string => nf.format(n ?? 0);

export function ago(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const seconds = (Date.now() - d.getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return d.toLocaleString();
}
