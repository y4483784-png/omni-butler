import { cancelCalendarEvent, updateCalendarEvent, type ScheduleCard as Card } from "../api/client";

function formatWhen(startAt: string, endAt: string): string {
  const start = startAt ? new Date(startAt) : null;
  const end = endAt ? new Date(endAt) : null;
  if (!start || Number.isNaN(start.getTime()) || !end || Number.isNaN(end.getTime())) {
    return "时间待补充";
  }
  return `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}-${String(start.getDate()).padStart(2, "0")} ${String(start.getHours()).padStart(2, "0")}:${String(start.getMinutes()).padStart(2, "0")} - ${String(end.getHours()).padStart(2, "0")}:${String(end.getMinutes()).padStart(2, "0")}`;
}

export function ScheduleCard({
  card,
  onChange,
}: {
  card: Card;
  onChange: (next: Card) => void;
}) {
  async function cancel() {
    const next = await cancelCalendarEvent(card.id);
    onChange(next);
  }

  async function edit() {
    const title = window.prompt("修改标题", card.title) ?? card.title;
    const start_at = window.prompt("修改开始时间（ISO）", card.start_at) ?? card.start_at;
    const end_at = window.prompt("修改结束时间（ISO）", card.end_at) ?? card.end_at;
    const next = await updateCalendarEvent(card.id, { title, start_at, end_at, participants: card.participants });
    onChange(next);
  }

  return (
    <div className={`schedule-card ${card.status !== "active" ? "is-cancelled" : ""}`}>
      <div className="schedule-head">
        <strong>{card.title}</strong>
        <span className="schedule-status">{card.status === "active" ? "已安排" : "已撤销"}</span>
      </div>
      <div className="schedule-meta">{formatWhen(card.start_at, card.end_at)}</div>
      {card.participants.length ? (
        <div className="schedule-meta">参与人：{card.participants.join("、")}</div>
      ) : null}
      <div className="schedule-actions">
        <button type="button" className="ghost-btn" onClick={edit} disabled={card.status !== "active"}>
          修改
        </button>
        <button type="button" className="ghost-btn danger" onClick={cancel} disabled={card.status !== "active"}>
          撤销
        </button>
      </div>
    </div>
  );
}
