import { useRef, useState } from "react";
import { connectGoogle, createCalendarEvent, type FollowAction, type ScheduleCard } from "../api/client";
import { isExpired } from "../utils/scheduleExpiry";
import { claimFollowRows, formatFollowUtterance, mailtoHref } from "../utils/followMail";

const NEEDS_PASTE = "把纪要或转写贴在这条消息里。这次没有整理待办，也没有编造纪要。";
const OFFICIAL_PASTE = "这场会还没有官方纪要。把会议记录或转写贴过来。不会编造不存在的纪要。";
const ATTACHMENT_CONSENT = "日历上有纪要附件。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。";
const MEET_CONSENT = "这场会有 Meet 智能纪要。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。";
const UNAVAILABLE = "暂时读不到这场会的官方纪要。可以把记录贴过来。不会编造官方纪要。";
const NOT_FOUND = "没有找到这场会。这次没有读官方纪要，也没有编造纪要。";

function copyFollowMail(mail: { subject: string; body: string }): void {
  const text = [mail.subject, mail.body].filter((part) => part.length > 0).join("\n");
  void navigator.clipboard.writeText(text);
}

function ownerLabel(owner: string): string {
  return owner || "未指定";
}

export function FollowupCard({ card, onAsk }: { card: ScheduleCard; onAsk?: (text: string) => void }) {
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
    const text = Array.isArray(card.checked) ? OFFICIAL_PASTE : NEEDS_PASTE;
    return (
      <div className="schedule-card">
        <p>{text}</p>
      </div>
    );
  }

  if (card.status === "needs_consent") {
    const text = card.consent === "meet" ? MEET_CONSENT : ATTACHMENT_CONSENT;
    return (
      <div className="schedule-card">
        <p>{text}</p>
        <button type="button" className="ghost-btn" onClick={() => connectGoogle()}>
          授权读取 Drive
        </button>
      </div>
    );
  }

  if (card.status === "unavailable") {
    return (
      <div className="schedule-card">
        <p>{UNAVAILABLE}</p>
      </div>
    );
  }

  if (card.status === "not_found") {
    return (
      <div className="schedule-card">
        <p>{NOT_FOUND}</p>
      </div>
    );
  }

  if (card.status === "choose") {
    const candidates = card.candidates ?? [];
    return (
      <div className="schedule-card">
        <div className="schedule-slots">
          {candidates.map((item) => (
            <button
              key={`${item.title}-${item.start_at}`}
              type="button"
              className="ghost-btn"
              onClick={() => onAsk?.(formatFollowUtterance(item))}
            >
              {item.title}
            </button>
          ))}
        </div>
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
      {sourceLine(card.source)}
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

function sourceLine(source: ScheduleCard["source"]) {
  const label =
    source?.kind === "meet_smart_notes" ? "来自 Meet" : source?.kind === "attachment_notes" ? "来自日程附件" : "";
  if (!label) return null;
  const title = source?.title || "";
  return (
    <p className="schedule-meta">
      {label}{" "}
      {source?.url ? (
        <a href={source.url} target="_blank" rel="noreferrer">
          {title}
        </a>
      ) : (
        <span>{title}</span>
      )}
    </p>
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
