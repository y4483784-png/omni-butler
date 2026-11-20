import { useState } from "react";
import { acknowledgeUnknown } from "../api/client";

export function AcknowledgeCard({
  card,
}: {
  card: { call_id?: string; generation?: number; sentence?: string };
}) {
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");
  const callId = String(card.call_id ?? "");
  const generation = Number(card.generation ?? 0);
  const sentence = String(card.sentence ?? "");

  async function submit() {
    setError("");
    try {
      const result = await acknowledgeUnknown(generation, callId);
      setAccepted(Boolean(result.accepted));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not acknowledge the unresolved call");
    }
  }

  return (
    <div data-mode="acknowledge" data-call-id={callId}>
      <p>{sentence}</p>
      <button type="button" onClick={() => void submit()} disabled={accepted || !callId}>
        {accepted ? "Acknowledged" : "Acknowledge and allow one new attempt"}
      </button>
      {error ? <p>{error}</p> : null}
    </div>
  );
}
