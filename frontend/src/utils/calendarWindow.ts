/** Seven-day window starting at 00:00 in an IANA zone, as naive wall-clock stamps. */

export interface CalendarWindow {
  start: string;
  end: string;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function stamp(year: number, month: number, day: number): string {
  return `${year}-${pad(month)}-${pad(day)}T00:00:00`;
}

export function sevenDayWindowInTimeZone(timeZone: string, now = new Date()): CalendarWindow | null {
  const zone = (timeZone || "").trim();
  if (!zone) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(now);
    const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
    const year = Number(value("year"));
    const month = Number(value("month"));
    const day = Number(value("day"));
    if (!year || !month || !day) return null;
    const end = new Date(Date.UTC(year, month - 1, day + 7));
    return {
      start: stamp(year, month, day),
      end: stamp(end.getUTCFullYear(), end.getUTCMonth() + 1, end.getUTCDate()),
    };
  } catch {
    return null;
  }
}
