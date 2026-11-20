import { useEffect, useRef, useState } from "react";
import { useStore } from "../store/useStore";
import { hydrateSessionMessages, startChatStream } from "../services/chatStream";
import { watchSession } from "../services/sessionWatch";
import type { ChatMsg, MeetingBrief } from "../api/client";
import { imageFromFile } from "../chatImage";
import {
  IMAGE_READ_ERROR,
  beginSend,
  completeComposerRead,
  createComposer,
  rejectSend,
  releasePreviewUrls,
  removeComposerImage,
  setComposerText,
  switchComposerSession,
  type ComposerImage,
} from "../composerImages";
import { Markdown } from "./Markdown";
import { FollowupCard } from "./FollowupCard";
import { ErrandCard } from "./ErrandCard";
import { MailSendCard } from "./MailSendCard";
import { MeetingBriefCard } from "./MeetingBriefCard";
import { AcknowledgeCard } from "./AcknowledgeCard";
import { ScheduleCard } from "./ScheduleCard";
import { UpcomingEvents } from "./UpcomingEvents";
import { MessageActions } from "./MessageActions";
import { artifactButtonLabel, artifactFromPayload } from "../utils/artifact";
import { foldThinking } from "../thinkingFold";

const MAX_CHARS = 10000;

export function ChatWindow({ sessionId }: { sessionId: number }) {
  const composerRef = useRef(createComposer());
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<ComposerImage[]>([]);
  const [imageError, setImageError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const readersRef = useRef<FileReader[]>([]);

  function publish(next: ReturnType<typeof createComposer>) {
    composerRef.current = next;
    setInput(next.text);
    setPending(next.pending);
  }

  const chat = useStore((s) => s.sessionChats[sessionId]);
  const messages = chat?.messages ?? [];
  const busy = chat?.busy ?? false;
  const thinking = chat?.thinking ?? false;
  const thinkingSteps = chat?.thinkingSteps ?? [];
  const routeHint = chat?.routeHint ?? "";
  const phaseHint = chat?.phaseHint ?? "";
  const jobs = chat?.jobs ?? [];

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
    return watchSession(sessionId);
  }, [sessionId]);

  useEffect(() => {
    const switched = switchComposerSession(composerRef.current);
    publish(switched.composer);
    releasePreviewUrls(switched.releaseUrls, releaseUrl);
    setImageError("");
    return () => {
      for (const reader of readersRef.current) reader.abort();
      readersRef.current = [];
      const closed = switchComposerSession(composerRef.current);
      publish(closed.composer);
      releasePreviewUrls(closed.releaseUrls, releaseUrl);
      setImageError("");
    };
  }, [sessionId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinkingSteps, sessionId]);

  function releaseUrl(url: string) {
    URL.revokeObjectURL(url);
  }

  async function send() {
    const started = beginSend(composerRef.current, busy);
    if (!started) return;
    for (const reader of readersRef.current) reader.abort();
    readersRef.current = [];
    publish(started.composer);
    releasePreviewUrls(started.releaseUrls, releaseUrl);
    setImageError("");
    const result = await startChatStream(sessionId, started.claim.text, {
      useKb,
      kbFocusIds,
      onTitle: updateSessionTitle,
      images: started.claim.images,
      localImages: started.claim.localImages,
    });
    if (result.accepted) return;
    const rejected = rejectSend(composerRef.current, started.claim, result.detail);
    publish(rejected.composer);
    releasePreviewUrls(rejected.releaseUrls, releaseUrl);
    if (rejected.error) setImageError(rejected.error);
  }

  function removePending(id: string) {
    const decision = removeComposerImage(composerRef.current, id);
    publish(decision.composer);
    if (decision.releaseUrl) releaseUrl(decision.releaseUrl);
  }

  function addFiles(files: FileList | null) {
    if (!files) return;
    const ticket = composerRef.current.ticket;
    const chosen = Array.from(files);
    const slots: Array<ComposerImage | undefined> = [];
    let rejected = false;
    const readers = chosen.map(
      (file, index) =>
        new Promise<void>((resolve) => {
          const reader = new FileReader();
          readersRef.current.push(reader);
          reader.onabort = () => resolve();
          reader.onload = () => {
            const buffer = reader.result;
            if (!(buffer instanceof ArrayBuffer)) {
              rejected = true;
              resolve();
              return;
            }
            const image = imageFromFile(file, new Uint8Array(buffer));
            if (image === null) {
              rejected = true;
              resolve();
              return;
            }
            const url = URL.createObjectURL(file);
            if (ticket !== composerRef.current.ticket) {
              releaseUrl(url);
              resolve();
              return;
            }
            slots[index] = {
              id: crypto.randomUUID(),
              mediaType: image.media_type,
              dataBase64: image.data_base64,
              url,
            };
            resolve();
          };
          reader.onerror = () => {
            rejected = true;
            resolve();
          };
          reader.readAsArrayBuffer(file);
        })
    );
    void Promise.all(readers).then(() => {
      const accepted = slots.filter((item): item is ComposerImage => item !== undefined);
      const decision = completeComposerRead(composerRef.current, ticket, accepted, rejected);
      releasePreviewUrls(decision.releaseUrls, releaseUrl);
      if (!decision.applied) return;
      publish(decision.composer);
      setImageError(decision.showError ? IMAGE_READ_ERROR : "");
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
      <UpcomingEvents />
      <div className="messages">
        {jobs
          .filter((job) => job.status === "running" || job.status === "waiting_person")
          .map((job) => (
            <p key={job.id} className="schedule-meta">
              {job.status} {job.goal}
              {job.unresolved_call_id ? ` ${job.unresolved_call_id}` : ""}
              {job.unresolved_sentence ? ` ${job.unresolved_sentence}` : ""}
            </p>
          ))}
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
            onToggleReasoning={() => {
              const copy = [...messages];
              const current = copy[i]?.fold ?? "hidden";
              copy[i] = { ...copy[i], fold: foldThinking(current, { type: "toggle" }) };
              patchSessionChat(sessionId, { messages: copy });
            }}
            onFeedbackChange={(rating) => {
              const copy = [...messages];
              copy[i] = { ...copy[i], feedback: rating };
              patchSessionChat(sessionId, { messages: copy });
            }}
            onTitle={updateSessionTitle}
            onOpenArtifact={(a) => openArtifact(artifactFromPayload(a, `msg-${i}`))}
            onAsk={(text) => {
              if (!text || busy) return;
              void startChatStream(sessionId, text, {
                useKb,
                kbFocusIds,
                onTitle: updateSessionTitle,
              });
            }}
          />
        ))}
        <div ref={endRef} />
      </div>
      <div className="composer-wrap">
        {pending.length > 0 ? (
          <div className="composer-images">
            {pending.map((item) => (
              <div className="composer-image" key={item.id}>
                <img src={item.url} alt="" />
                <button type="button" aria-label="移除图片" onClick={() => removePending(item.id)}>
                  ×
                </button>
              </div>
            ))}
          </div>
        ) : null}
        {imageError ? <p className="composer-image-error">{imageError}</p> : null}
        <div className="composer">
          <textarea
            value={input}
            maxLength={MAX_CHARS}
            placeholder={
              useKb
                ? "强制知识库：基于已上传文档提问…"
                : "输入消息，Enter 发送 / Shift+Enter 换行（可自动走知识库）"
            }
            onChange={(e) => publish(setComposerText(composerRef.current, e.target.value))}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/gif,image/webp"
            multiple
            hidden
            onChange={(event) => {
              addFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <button type="button" className="attach-btn" onClick={() => fileRef.current?.click()}>
            图片
          </button>
          <button disabled={busy || (!input.trim() && pending.length === 0)} onClick={send}>
            发送
          </button>
        </div>
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
  onToggleReasoning,
  onFeedbackChange,
  onOpenArtifact,
  onTitle,
  onAsk,
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
  onToggleReasoning: () => void;
  onFeedbackChange: (rating: "up" | "down" | null) => void;
  onOpenArtifact: (a: NonNullable<ChatMsg["artifact"]>) => void;
  onTitle: (sessionId: number, title: string) => void;
  onAsk: (text: string) => void;
}) {
  const isStreaming = m.role === "assistant" && isLast && thinking && !m.content;

  return (
    <div className={`row ${m.role}`}>
      <div className="bubble">
        {m.localImages && m.localImages.length > 0 ? (
          <div className="bubble-images">
            {m.localImages.map((item, index) => (
              <img key={`${item.url}-${index}`} src={item.url} alt="" />
            ))}
          </div>
        ) : null}
        {m.role === "assistant" ? (
          <>
            {isLast && thinkingSteps.length > 0 ? (
              <ul className="thinking-steps">
                {thinkingSteps.map((s, si) => (
                  <li key={si}>{s}</li>
                ))}
              </ul>
            ) : null}
            {m.fold && m.fold !== "hidden" ? (
              <div className="reasoning-block">
                <button type="button" onClick={onToggleReasoning}>
                  思考
                </button>
                {m.fold === "streaming" || m.fold === "opened" ? (
                  <pre className="reasoning-text">{m.reasoning || ""}</pre>
                ) : null}
              </div>
            ) : null}
            {m.scheduleCard?.mode === "acknowledge" ? (
              <AcknowledgeCard card={m.scheduleCard} />
            ) : m.scheduleCard?.mode === "list" ? (
              <ScheduleCard card={m.scheduleCard} onChange={onScheduleChange} onAsk={onAsk} sessionId={sessionId} messageId={m.id} />
            ) : m.scheduleCard?.mode === "brief" ? (
              <MeetingBriefCard card={m.scheduleCard as MeetingBrief} onAsk={onAsk} />
            ) : m.scheduleCard?.mode === "followup" ? (
              <FollowupCard card={m.scheduleCard} onAsk={onAsk} sessionId={sessionId} />
            ) : m.scheduleCard?.mode === "send" ? (
              <MailSendCard card={m.scheduleCard} onChange={onScheduleChange} />
            ) : m.scheduleCard?.mode === "errand" ? (
              <ErrandCard card={m.scheduleCard} onChange={onScheduleChange} />
            ) : m.scheduleCard?.mode === "desktop" ? (
              <p
                data-mode="desktop"
                data-confirm-token={m.scheduleCard.confirm_token ?? ""}
                data-action={m.scheduleCard.action ?? ""}
                data-local-target={m.scheduleCard.local_target ?? ""}
                data-local-args={JSON.stringify(m.scheduleCard.local_args ?? [])}
                data-device-id={String(m.scheduleCard.device_id ?? "")}
              >
                {m.scheduleCard.device_label} {m.scheduleCard.local_target} {m.scheduleCard.action}
              </p>
            ) : null}
            {m.content ? (
              <Markdown content={m.content} citations={m.citations} />
            ) : thinking && isLast ? (
              <span className="thinking">
                思考中<span className="dot" /><span className="dot" /><span className="dot" />
              </span>
            ) : m.fold === "streaming" || m.scheduleCard ? null : (
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
            {m.scheduleCard && m.scheduleCard.mode !== "list" && m.scheduleCard.mode !== "followup" && m.scheduleCard.mode !== "send" && m.scheduleCard.mode !== "desktop" && m.scheduleCard.mode !== "errand" && m.scheduleCard.mode !== "acknowledge" && m.scheduleCard.mode !== "brief" ? (
              <ScheduleCard card={m.scheduleCard} onChange={onScheduleChange} sessionId={sessionId} messageId={m.id} />
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
