import { useState } from "react";
import { commitErrand, type ScheduleCard } from "../api/client";

export function ErrandCard({
  card,
  onChange,
}: {
  card: ScheduleCard;
  onChange: (next: ScheduleCard) => void;
}) {
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const errand = card.errand;
  const confirmed = card.phase === "confirmed";

  async function confirm() {
    if (!errand || confirmed || posting) return;
    setPosting(true);
    try {
      await commitErrand({
        merchant_key: errand.merchant_key,
        lines: errand.lines,
        ship_to: errand.ship_to,
        pay_method: errand.pay_method,
        amount_cap: errand.amount_cap,
        confirm_token: card.confirm_token ?? "",
      });
      onChange({ ...card, phase: "confirmed" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "下单失败");
    } finally {
      setPosting(false);
    }
  }

  if (!errand) return null;

  const address = [errand.ship_to.region, errand.ship_to.address].filter(Boolean).join(" ");

  return (
    <div className="schedule-card">
      <div className="schedule-head">
        <strong>{errand.merchant_key}</strong>
      </div>
      {errand.lines.map((line, index) => (
        <div className="schedule-meta" key={`${line.sku}-${index}`}>
          {line.query || line.sku} × {line.qty}
        </div>
      ))}
      {address ? <div className="schedule-meta">{address}</div> : null}
      <div className="schedule-meta">{errand.pay_method}</div>
      <div className="schedule-meta">金额上限 {errand.amount_cap}</div>
      {confirmed ? null : (
        <div className="schedule-actions">
          <button type="button" className="ghost-btn" disabled={posting} onClick={() => void confirm()}>
            确认下单
          </button>
        </div>
      )}
      {error ? <div className="schedule-note">{error}</div> : null}
    </div>
  );
}
