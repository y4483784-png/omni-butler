import { useRef, useState } from "react";
import { createCalendarEvent, type FollowAction, type ScheduleCard } from "../api/client";
import { isExpired } from "../utils/scheduleExpiry";
import { claimFollowRows, mailtoHref } from "../utils/followMail";

const NEEDS_PASTE = "把纪要或转写贴在这条消息里。这次没有整理待办，也没有编造纪要。";

function copyFollowMail(mail: { subject: string; body: string }): void {
  const text = [mail.subject, mail.body].filter((part) => part.length > 0).join("\n");
  void navigator.clipboard.writeText(text);
}

function ownerLabel(owner: string): string {
  return owner || "未指定";
}

export function FollowupCard({ card }: { card: ScheduleCard }) {
  const expired = isExpired(card);
  const actions = card.actions ?? [];
  const points = card.points ?? [];
  const mail = card.mail;
  const [ticked, setTicked] = useState<ReadonlySet<string>>(() => new Set());
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const inflight = useRef(false);
  const done = useRef(new Set<string>());

  if (card.status === "needs_paste") {
    return (
      <div className="schedule-card">
        <p>{NEEDS_PASTE}</p>
      </div>
    );
  }

  function toggle(id: string) {
    setTicked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function confirm() {
    if (expired) return;
    const picked = actions.filter((action) => ticked.has(action.id) && action.start_at).map((action) => action.id);
    const ids = claimFollowRows(picked, done.current, inflight);
    if (!ids.length) return;
    setPosting(true);
    let failed = false;
    try {
      for (const id of ids) {
        const action = actions.find((item) => item.id === id);
        if (!action) continue;
        try {
          await postFollowAction(action);
          done.current.add(id);
        } catch {
          failed = true;
        }
      }
    } finally {
      inflight.current = false;
      setPosting(false);
    }
    setError(failed ? "有待办没有加进日历。" : "");
  }

  return (
    <div className="schedule-card">
      {expired ? <div className="schedule-status">已过期</div> : null}
      {points.length ? (
        <ul className="schedule-list">
          {points.map((point) => (
            <li key={point}>{point}</li>
          ))}
        </ul>
      ) : null}
      {actions.length ? (
        <ul className="schedule-list">
          {actions.map((action) => (
            <li key={action.id}>
              {action.start_at ? (
                <input
                  type="checkbox"
                  checked={ticked.has(action.id)}
                  disabled={posting}
                  onChange={() => toggle(action.id)}
                />
              ) : null}
              <span>{action.text}</span>
              <span>{ownerLabel(action.owner)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {mail ? (
        <div>
          <div className="schedule-head">
            <strong>{mail.subject}</strong>
          </div>
          {mail.body ? <pre className="schedule-meta">{mail.body}</pre> : null}
          <div className="schedule-actions">
            <button type="button" className="ghost-btn" onClick={() => copyFollowMail(mail)}>
              复制邮件
            </button>
            <a className="ghost-btn" href={mailtoHref(mail)}>
              用邮件应用打开
            </a>
          </div>
        </div>
      ) : null}
      {card.phase === "draft" ? (
        <div className="schedule-actions">
          <button type="button" className="ghost-btn" disabled={posting || expired} onClick={() => void confirm()}>
            确认加到日历
          </button>
        </div>
      ) : null}
      {error ? <div className="schedule-note">{error}</div> : null}
    </div>
  );
}

async function postFollowAction(action: FollowAction): Promise<void> {
  await createCalendarEvent({
    title: `跟进：${action.text}`,
    start_at: action.start_at,
    end_at: action.end_at,
    participants: [],
    meet: false,
    description: `follow_action: ${action.id}\n${action.evidence}`,
  });
}
