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

function formatAllDay(startAt: string, endAt: string): string {
  const startDay = (startAt || "").slice(0, 10);
  const endDay = (endAt || "").slice(0, 10);
  if (!startDay) return "全天";
  if (!endDay || endDay <= startDay) return `${startDay} 全天`;
  const end = new Date(`${endDay}T00:00:00`);
  end.setDate(end.getDate() - 1);
  const last = `${end.getFullYear()}-${pad(end.getMonth() + 1)}-${pad(end.getDate())}`;
  if (!last || last === startDay) return `${startDay} 全天`;
  return `${startDay}–${last} 全天`;
}

function fromGoogle(event: ScheduleCard): boolean {
  return event.readonly === true || event.id == null;
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
  const [notice, setNotice] = useState("");

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
        const listed = await listCalendarEvents(range.start, range.end);
        if (!cancelled) {
          setEvents(listed.events);
          setNotice(listed.notice);
        }
      } catch {
        if (!cancelled) {
          setEvents([]);
          setNotice("");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!events || (events.length === 0 && !notice)) return null;
  return (
    <aside className="upcoming-events" aria-label="近日日程">
      <div className="upcoming-title">近日日程</div>
      {notice ? <p className="upcoming-notice">{notice}</p> : null}
      <ul>
        {events.map((event) => {
          const googleOnly = fromGoogle(event);
          const when = event.all_day
            ? formatAllDay(event.start_at, event.end_at)
            : formatWhen(event.start_at, event.end_at);
          return (
            <li key={event.id ?? `${event.external_id || event.title}-${event.start_at}`}>
              <span>
                {event.title || "（无标题）"}
                {googleOnly ? <em className="upcoming-source">来自 Google</em> : null}
              </span>
              <span>{when}</span>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
