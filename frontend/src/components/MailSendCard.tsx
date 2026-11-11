import { useState } from "react";
import { commitMailSend, type ScheduleCard } from "../api/client";

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

  async function confirm() {
    if (!mail || confirmed || posting) return;
    setPosting(true);
    try {
      await commitMailSend({
        to: addresses(card),
        subject: mail.subject,
        body: mail.body,
        confirm_token: card.confirm_token ?? "",
      });
      onChange({ ...card, phase: "confirmed" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
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
      {confirmed ? null : (
        <div className="schedule-actions">
          <button type="button" className="ghost-btn" disabled={posting} onClick={() => void confirm()}>
            确认发送
          </button>
        </div>
      )}
      {error ? <div className="schedule-note">{error}</div> : null}
    </div>
  );
}
