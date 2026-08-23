/** ChatGPT / Open WebUI style recency buckets (PRD 3.1.2). */

export type SessionBucket = "今天" | "昨天" | "过去7天" | "更早";

const BUCKETS: SessionBucket[] = ["今天", "昨天", "过去7天", "更早"];

export function parseSessionTime(raw: string): Date {
  const s = (raw || "").trim();
  if (!s) return new Date(NaN);
  if (/Z$|[+-]\d{2}:\d{2}$/.test(s)) return new Date(s);
  return new Date(`${s}Z`);
}

export function sessionBucket(updatedAt: string, now: Date = new Date()): SessionBucket {
  const d = parseSessionTime(updatedAt);
  if (Number.isNaN(d.getTime())) return "更早";
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startYesterday = new Date(startToday);
  startYesterday.setDate(startYesterday.getDate() - 1);
  const startWeek = new Date(startToday);
  startWeek.setDate(startWeek.getDate() - 7);
  if (d >= startToday) return "今天";
  if (d >= startYesterday) return "昨天";
  if (d >= startWeek) return "过去7天";
  return "更早";
}

export function groupSessions<T extends { updated_at: string }>(
  sessions: T[],
  now: Date = new Date()
): { label: SessionBucket; sessions: T[] }[] {
  const map = new Map<SessionBucket, T[]>();
  for (const s of sessions) {
    const label = sessionBucket(s.updated_at, now);
    const arr = map.get(label) ?? [];
    arr.push(s);
    map.set(label, arr);
  }
  return BUCKETS.filter((label) => (map.get(label) || []).length > 0).map((label) => ({
    label,
    sessions: [...(map.get(label) || [])].sort(
      (a, b) => parseSessionTime(b.updated_at).getTime() - parseSessionTime(a.updated_at).getTime()
    ),
  }));
}
