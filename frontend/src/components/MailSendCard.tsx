import { useState } from "react";
import { MailSendError, commitMailSend, type ScheduleCard } from "../api/client";

function addresses(card: ScheduleCard): string[] {
  return (card.mail?.to ?? []).map((person) => person.email.trim()).filter((email) => email.includes("@"));
}

export function MailSendCard({
  card,
  onChange,
}: {
  card: ScheduleCard;
  onChange: (next: ScheduleCard) => void;
}) {
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const mail = card.mail;
  const confirmed = card.phase === "confirmed";
  const unknown = card.phase === "unknown";
  const phaseNote = confirmed ? "已发送" : unknown ? "结果未知，未再次发送。" : "";

  async function confirm() {
    if (!mail || confirmed || posting) return;
    setPosting(true);
    setError("");
    try {
      const result = await commitMailSend({
        to: addresses(card),
        subject: mail.subject,
        body: mail.body,
        confirm_token: card.confirm_token ?? "",
      });
      if (result.state === "sent") {
        onChange({ ...card, phase: "confirmed" });
        return;
      }
      setError("发送失败");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "发送失败";
      if (err instanceof MailSendError && err.state === "unknown") {
        onChange({ ...card, phase: "unknown" });
      }
      setError(detail);
    } finally {
      setPosting(false);
    }
  }

  if (!mail) return null;

  return (
    <div className="schedule-card">
      <div className="schedule-head">
        <strong>{mail.subject}</strong>
      </div>
      {mail.body ? <pre className="schedule-meta">{mail.body}</pre> : null}
      {phaseNote ? <div className="schedule-note">{phaseNote}</div> : null}
      {confirmed ? null : (
        <div className="schedule-actions">
          <button type="button" className="ghost-btn" disabled={posting} onClick={() => void confirm()}>
            确认发送
          </button>
        </div>
      )}
      {error && error !== phaseNote ? <div className="schedule-note">{error}</div> : null}
    </div>
  );
}
