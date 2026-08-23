export type ChartPoint = {
  label: string;
  series?: string;
  value: number;
  x?: number;
};

/** Map click/hover X ratio to the nearest category (bar/line/hist). */
export function pointsAtRatio(points: ChartPoint[], ratio: number): ChartPoint[] {
  if (!points.length) return [];
  const labels: string[] = [];
  const seen = new Set<string>();
  for (const p of points) {
    if (!seen.has(p.label)) {
      seen.add(p.label);
      labels.push(p.label);
    }
  }
  if (!labels.length) return [];
  const t = Math.min(1, Math.max(0, ratio));
  const idx = Math.round(t * (labels.length - 1));
  const label = labels[idx];
  return points.filter((p) => p.label === label);
}

export function formatChartValue(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  const abs = Math.abs(n);
  if (abs >= 1000 || Number.isInteger(n)) return String(Math.round(n * 100) / 100);
  return String(Math.round(n * 10000) / 10000);
}
