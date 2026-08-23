import { useEffect, useRef, useState } from "react";
import { useStore } from "../store/useStore";
import { hydrateSessionMessages, startChatStream, stopChatStream } from "../services/chatStream";
import type { ChatMsg } from "../api/client";
import { Markdown } from "./Markdown";
import { ScheduleCard } from "./ScheduleCard";
import { MessageActions } from "./MessageActions";
import { artifactButtonLabel, artifactFromPayload } from "../utils/artifact";

const MAX_CHARS = 10000; // PRD 3.1.1

export function ChatWindow({ sessionId }: { sessionId: number }) {
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const chat = useStore((s) => s.sessionChats[sessionId]);
  const messages = chat?.messages ?? [];
  const busy = chat?.busy ?? false;
  const thinking = chat?.thinking ?? false;
  const thinkingSteps = chat?.thinkingSteps ?? [];
  const routeHint = chat?.routeHint ?? "";
  const phaseHint = chat?.phaseHint ?? "";

  const updateSessionTitle = useStore((s) => s.updateSessionTitle);
  const openArtifact = useStore((s) => s.openArtifact);
  const patchSessionChat = useStore((s) => s.patchSessionChat);
  const useKb = useStore((s) => s.useKb);
  const setUseKb = useStore((s) => s.setUseKb);
  const setKbPanelOpen = useStore((s) => s.setKbPanelOpen);
  const setMemoryPanelOpen = useStore((s) => s.setMemoryPanelOpen);
  const kbFocusIds = useStore((s) => s.kbFocusIds);

  useEffect(() => {
    hydrateSessionMessages(sessionId);
  }, [sessionId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinkingSteps, sessionId]);

  function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    void startChatStream(sessionId, text, {
      useKb,
      kbFocusIds,
      onTitle: updateSessionTitle,
    });
  }

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <label className="kb-toggle" title="勾选后强制走知识库检索；不勾选时由 LangGraph 自动判断">
          <input
            type="checkbox"
            checked={useKb}
            onChange={(e) => setUseKb(e.target.checked)}
          />
          强制知识库
        </label>
        <button type="button" className="ghost-btn" onClick={() => setKbPanelOpen(true)}>
          管理文档
        </button>
        <button type="button" className="ghost-btn" onClick={() => setMemoryPanelOpen(true)}>
          记忆
        </button>
        {routeHint ? <span className="route-hint">{routeHint}</span> : null}
        {phaseHint ? <span className="route-hint phase-hint">{phaseHint}</span> : null}
      </div>
      <div className="messages">
        {messages.map((m, i) => (
          <MessageRow
            key={m.id ?? `local-${i}`}
            sessionId={sessionId}
            m={m}
            isLast={i === messages.length - 1}
            busy={busy}
            thinking={thinking}
            thinkingSteps={thinkingSteps}
            useKb={useKb}
            kbFocusIds={kbFocusIds}
            onScheduleChange={(next) => {
              const copy = [...messages];
              copy[i] = { ...copy[i], scheduleCard: next };
              patchSessionChat(sessionId, { messages: copy });
            }}
            onFeedbackChange={(rating) => {
              const copy = [...messages];
              copy[i] = { ...copy[i], feedback: rating };
              patchSessionChat(sessionId, { messages: copy });
            }}
            onTitle={updateSessionTitle}
            onOpenArtifact={(a) => openArtifact(artifactFromPayload(a, `msg-${i}`))}
          />
        ))}
        <div ref={endRef} />
      </div>
      <div className="composer">
        <textarea
          value={input}
          maxLength={MAX_CHARS}
          placeholder={
            busy
              ? "生成中，可点击停止…"
              : useKb
                ? "强制知识库：基于已上传文档提问…"
                : "输入消息，Enter 发送 / Shift+Enter 换行（可自动走知识库）"
          }
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        {busy ? (
          <button type="button" className="stop-btn" onClick={() => stopChatStream(sessionId)}>
            停止生成
          </button>
        ) : (
          <button disabled={!input.trim()} onClick={send}>
            发送
          </button>
        )}
      </div>
    </div>
  );
}

function MessageRow({
  sessionId,
  m,
  isLast,
  busy,
  thinking,
  thinkingSteps,
  useKb,
  kbFocusIds,
  onScheduleChange,
  onFeedbackChange,
  onOpenArtifact,
  onTitle,
}: {
  sessionId: number;
  m: ChatMsg;
  isLast: boolean;
  busy: boolean;
  thinking: boolean;
  thinkingSteps: string[];
  useKb: boolean;
  kbFocusIds: number[];
  onScheduleChange: (next: ChatMsg["scheduleCard"]) => void;
  onFeedbackChange: (rating: "up" | "down" | null) => void;
  onOpenArtifact: (a: NonNullable<ChatMsg["artifact"]>) => void;
  onTitle: (sessionId: number, title: string) => void;
}) {
  const isStreaming = m.role === "assistant" && isLast && thinking && !m.content;

  return (
    <div className={`row ${m.role}`}>
      <div className="bubble">
        {m.role === "assistant" ? (
          <>
            {isLast && thinkingSteps.length > 0 ? (
              <ul className="thinking-steps">
                {thinkingSteps.map((s, si) => (
                  <li key={si}>{s}</li>
                ))}
              </ul>
            ) : null}
            {m.content ? (
              <Markdown content={m.content} citations={m.citations} />
            ) : thinking && isLast ? (
              <span className="thinking">
                思考中<span className="dot" /><span className="dot" /><span className="dot" />
              </span>
            ) : (
              <span className="placeholder">…</span>
            )}
            {m.artifact ? (
              <button
                type="button"
                className="artifact-reopen-btn"
                onClick={() => onOpenArtifact(m.artifact!)}
              >
                {artifactButtonLabel(m.artifact)}
              </button>
            ) : null}
            {m.scheduleCard ? (
              <ScheduleCard card={m.scheduleCard} onChange={onScheduleChange} />
            ) : null}
            <MessageActions
              sessionId={sessionId}
              messageId={m.id}
              content={m.content}
              feedback={m.feedback}
              busy={busy}
              isStreaming={isStreaming}
              useKb={useKb}
              kbFocusIds={kbFocusIds}
              onFeedbackChange={onFeedbackChange}
            onTitle={onTitle}
          />
          </>
        ) : (
          <span>{m.content}</span>
        )}
      </div>
    </div>
  );
}
