import { connectGoogle, formatPointedUtterance, type BriefEmail } from "../api/client";
import { briefFromWire, consentLabel, viaLabel, type BriefPerson, type NormalizedBrief } from "../utils/briefFromWire";

function briefWhen(meeting: NonNullable<NormalizedBrief["meeting"]>): string {
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

function personLabel(person: BriefPerson): string {
  return person.name || person.email;
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
  card: Parameters<typeof briefFromWire>[0];
  onAsk?: (text: string) => void;
}) {
  const view = briefFromWire(card);
  const meeting = view.meeting;
  const emails = view.mail.emails;
  const focus = view.mail.focus
    .map((item) => focusLine(emails, item.message_id, item.sentence_index))
    .filter((line) => line.length > 0);
  const candidates = view.candidates;
  const button = consentLabel(view);

  return (
    <div className="schedule-card">
      {meeting ? (
        <div>
          <div className="schedule-head">
            <strong>{meeting.title || "（无标题）"}</strong>
            <span className="schedule-status">这场会</span>
          </div>
          <div className="schedule-meta">{briefWhen(meeting)}</div>
          {meeting.attendees.length ? (
            <div className="schedule-meta">{meeting.attendees.map(personLabel).join("、")}</div>
          ) : null}
        </div>
      ) : null}
      {view.status === "ready" ? (
        <div>
          <div className="schedule-head">
            <strong>相关邮件</strong>
          </div>
          {view.mail.state === "found" ? (
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
          ) : null}
          {view.mail.state === "empty" ? <div className="schedule-meta">没有和这场会相关的邮件。</div> : null}
          {view.mail.state === "unavailable" ? (
            <div className="schedule-meta">暂时读不到 Gmail。这次没有邮件内容。</div>
          ) : null}
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
          <div className="schedule-head">
            <strong>相关文档</strong>
          </div>
          {view.docs.state === "found" ? (
            <ul className="schedule-list">
              {view.docs.docs.map((doc) => (
                <li key={doc.file_id} className="brief-mail">
                  <span>{viaLabel(doc.via)}</span>
                  {doc.url ? (
                    <a href={doc.url} target="_blank" rel="noreferrer">
                      {doc.title || "打开文档"}
                    </a>
                  ) : (
                    <span>{doc.title}</span>
                  )}
                  {doc.sentences[0] ? <span className="schedule-meta">{doc.sentences[0]}</span> : null}
                  {doc.sentences[1] ? <span className="schedule-meta">{doc.sentences[1]}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
          {view.docs.state === "empty" ? <div className="schedule-meta">没有找到相关文档。</div> : null}
          {view.docs.state === "unavailable" ? (
            <div className="schedule-meta">暂时读不到云端硬盘。这次没有文档。</div>
          ) : null}
        </div>
      ) : null}
      {button ? (
        <button type="button" className="ghost-btn" onClick={() => connectGoogle()}>
          {button}
        </button>
      ) : null}
      {view.status === "choose" && candidates.length ? (
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
      {view.status === "not_found" ? (
        <div className="schedule-meta">
          {view.said ? `没有找到「${view.said}」这场会。` : "要准备哪一场会？点一场会的会前准备，或说出会议名称。"}
        </div>
      ) : null}
    </div>
  );
}
