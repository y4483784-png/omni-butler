import { useState } from "react";
import { setMessageFeedback } from "../api/client";
import { startRegenerateStream } from "../services/chatStream";

type Props = {
  sessionId: number;
  messageId?: number;
  content: string;
  feedback?: "up" | "down" | null;
  busy: boolean;
  isStreaming: boolean;
  useKb: boolean;
  kbFocusIds: number[];
  onFeedbackChange: (rating: "up" | "down" | null) => void;
  onTitle?: (sessionId: number, title: string) => void;
};

export function MessageActions({
  sessionId,
  messageId,
  content,
  feedback,
  busy,
  isStreaming,
  useKb,
  kbFocusIds,
  onFeedbackChange,
  onTitle,
}: Props) {
  const [copied, setCopied] = useState(false);

  if (isStreaming || !content.trim()) return null;

  async function copyContent() {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard denied */
    }
  }

  async function regenerate() {
    if (!messageId || busy) return;
    await startRegenerateStream(sessionId, messageId, { useKb, kbFocusIds, onTitle });
  }

  async function vote(next: "up" | "down") {
    if (!messageId) return;
    const rating = feedback === next ? null : next;
    const prev = feedback ?? null;
    onFeedbackChange(rating);
    try {
      await setMessageFeedback(messageId, rating);
    } catch {
      onFeedbackChange(prev);
    }
  }

  return (
    <div className="message-actions">
      <button type="button" className="msg-action-btn" onClick={copyContent}>
        {copied ? "已复制" : "复制"}
      </button>
      <button
        type="button"
        className="msg-action-btn"
        disabled={busy || !messageId}
        onClick={() => void regenerate()}
      >
        重新生成
      </button>
      <button
        type="button"
        className={`msg-action-btn ${feedback === "up" ? "active-up" : ""}`}
        disabled={!messageId}
        onClick={() => void vote("up")}
        title="有帮助"
      >
        赞
      </button>
      <button
        type="button"
        className={`msg-action-btn ${feedback === "down" ? "active-down" : ""}`}
        disabled={!messageId}
        onClick={() => void vote("down")}
        title="无帮助"
      >
        踩
      </button>
    </div>
  );
}
