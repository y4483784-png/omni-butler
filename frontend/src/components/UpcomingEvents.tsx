import { useEffect, useState } from "react";
import { googleCalendarStatus, listCalendarEvents, localStampFromDate, type ScheduleCard } from "../api/client";
import { sevenDayWindowInTimeZone } from "../utils/calendarWindow";

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function formatWhen(startAt: string, endAt: string): string {
  const start = startAt ? new Date(startAt) : null;
  const end = endAt ? new Date(endAt) : null;
  if (!start || Number.isNaN(start.getTime())) return startAt;
  const day = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())} ${pad(start.getHours())}:${pad(start.getMinutes())}`;
  if (!end || Number.isNaN(end.getTime())) return day;
  return `${day}–${pad(end.getHours())}:${pad(end.getMinutes())}`;
}

function browserSevenDayWindow(): { start: string; end: string } {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 7);
  return { start: localStampFromDate(start), end: localStampFromDate(end) };
}

export function UpcomingEvents() {
  const [events, setEvents] = useState<ScheduleCard[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let range = browserSevenDayWindow();
      try {
        const status = await googleCalendarStatus();
        if (status.connected && status.timezone) {
          range = sevenDayWindowInTimeZone(status.timezone) ?? range;
        }
      } catch {
        range = browserSevenDayWindow();
      }
      if (cancelled) return;
      try {
        const rows = await listCalendarEvents(range.start, range.end);
        if (!cancelled) setEvents(rows);
      } catch {
        if (!cancelled) setEvents([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!events || events.length === 0) return null;
  return (
    <aside className="upcoming-events" aria-label="近日日程">
      <div className="upcoming-title">近日日程</div>
      <ul>
        {events.map((event) => (
          <li key={event.id ?? `${event.title}-${event.start_at}`}>
            <span>{event.title || "未命名日程"}</span>
            <span>{formatWhen(event.start_at, event.end_at)}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}
