import { connectGoogle, formatPointedUtterance, type BriefEmail, type BriefMeeting, type MeetingBrief } from "../api/client";

function briefWhen(meeting: BriefMeeting): string {
  const start = meeting.start_at || "";
  if (meeting.all_day || start.length < 16) {
    const day = start.slice(0, 10);
    return day ? `${day} 全天` : "全天";
  }
  const end = meeting.end_at || "";
  const endClock = end.length >= 16 ? end.slice(11, 16) : "";
  const startLabel = `${start.slice(0, 10)} ${start.slice(11, 16)}`;
  return endClock ? `${startLabel} - ${endClock}` : startLabel;
}

function sentLabel(value: string): string {
  if (!value) return "";
  return value.slice(0, 16).replace("T", " ");
}

function focusLine(emails: BriefEmail[], messageId: string, sentenceIndex: number): string {
  const email = emails.find((item) => item.message_id === messageId);
  const sentence = email?.sentences?.[sentenceIndex];
  return typeof sentence === "string" ? sentence : "";
}

export function MeetingBriefCard({
  card,
  onAsk,
}: {
  card: MeetingBrief;
  onAsk?: (text: string) => void;
}) {
  const meeting = card.meeting;
  const emails = card.emails ?? [];
  const focus = (card.focus ?? [])
    .map((item) => focusLine(emails, item.message_id, item.sentence_index))
    .filter((line) => line.length > 0);
  const candidates = card.candidates ?? [];

  return (
    <div className="schedule-card">
      {meeting ? (
        <div>
          <div className="schedule-head">
            <strong>{meeting.title || "（无标题）"}</strong>
            <span className="schedule-status">这场会</span>
          </div>
          <div className="schedule-meta">{briefWhen(meeting)}</div>
          {meeting.attendees?.length ? (
            <div className="schedule-meta">{meeting.attendees.join("、")}</div>
          ) : null}
        </div>
      ) : null}
      {card.status === "ready" ? (
        <div>
          <div className="schedule-head">
            <strong>相关邮件</strong>
          </div>
          <ul className="schedule-list">
            {emails.map((email) => (
              <li key={email.message_id} className="brief-mail">
                <span>
                  {email.sender}
                  {email.sent_at ? ` · ${sentLabel(email.sent_at)}` : ""}
                </span>
                {email.url ? (
                  <a href={email.url} target="_blank" rel="noreferrer">
                    {email.subject || "打开邮件"}
                  </a>
                ) : (
                  <span>{email.subject}</span>
                )}
                {email.sentences?.[0] ? <span className="schedule-meta">{email.sentences[0]}</span> : null}
                {email.sentences?.[1] ? <span className="schedule-meta">{email.sentences[1]}</span> : null}
              </li>
            ))}
          </ul>
          {focus.length ? (
            <div>
              <div className="schedule-head">
                <strong>建议关注</strong>
              </div>
              <ul className="brief-focus">
                {focus.map((line, index) => (
                  <li key={`${index}-${line}`}>{line}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
      {card.status === "empty" ? <div className="schedule-meta">没有和这场会相关的邮件。</div> : null}
      {card.status === "unavailable" ? <div className="schedule-meta">暂时读不到 Gmail。这次没有邮件内容。</div> : null}
      {card.status === "needs_consent" ? (
        <button type="button" className="ghost-btn" onClick={() => connectGoogle()}>
          {card.connected ? "授权读取 Gmail" : "连接 Google 账户"}
        </button>
      ) : null}
      {card.status === "choose" && candidates.length ? (
        <div className="schedule-slots">
          {candidates.map((item) => (
            <button
              key={`${item.start_at}-${item.title}`}
              type="button"
              className="ghost-btn"
              onClick={() => onAsk?.(formatPointedUtterance(item))}
            >
              {item.title} {briefWhen(item)}
            </button>
          ))}
        </div>
      ) : null}
      {card.status === "not_found" ? (
        <div className="schedule-meta">
          {card.said ? `没有找到「${card.said}」这场会。` : "要准备哪一场会？点一场会的会前准备，或说出会议名称。"}
        </div>
      ) : null}
    </div>
  );
}
