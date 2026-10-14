import { useEffect, useState } from "react";
import { listCalendarEvents, localStampFromDate, type ScheduleCard } from "../api/client";

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

export function UpcomingEvents() {
  const [events, setEvents] = useState<ScheduleCard[] | null>(null);

  useEffect(() => {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(end.getDate() + 7);
    let cancelled = false;
    listCalendarEvents(localStampFromDate(start), localStampFromDate(end))
      .then((rows) => {
        if (!cancelled) setEvents(rows);
      })
      .catch(() => {
        if (!cancelled) setEvents([]);
      });
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
