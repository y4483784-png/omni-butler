import { streamChat, loadMessages } from "../api/client";
import type { ChatMsg, Citation, ScheduleCard, ArtifactPayload } from "../api/client";
import { useStore, type SessionChatState } from "../store/useStore";
import { artifactFromPayload } from "../utils/artifact";
import { foldThinking, type ThinkingFold } from "../thinkingFold";
import { overlayUnsavedError } from "../unsavedError";

const abortControllers = new Map<number, AbortController>();

export type StartChatOptions = {
  useKb: boolean;
  kbFocusIds: number[];
  onTitle?: (sessionId: number, title: string) => void;
};

function isAbortError(e: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" && e instanceof DOMException && e.name === "AbortError") ||
    (e instanceof Error && e.name === "AbortError")
  );
}

export function stopChatStream(sessionId: number): void {
  abortControllers.get(sessionId)?.abort();
}

export function abortAllChatStreams(): void {
  for (const ac of abortControllers.values()) {
    ac.abort();
  }
  abortControllers.clear();
}

function withHydratedFold(before: ChatMsg[], synced: ChatMsg[]): ChatMsg[] {
  return synced.map((row, i) => {
    const local =
      (row.id != null ? before.find((message) => message.id === row.id) : undefined) ?? before[i];
    const fold = foldThinking(local?.fold ?? "hidden", {
      type: "hydrate",
      reasoning: row.reasoning || "",
      content: row.content || "",
    });
    return { ...row, fold };
  });
}

function emptyChatState(): SessionChatState {
  return {
    messages: [],
    busy: false,
    thinking: false,
    thinkingSteps: [],
    routeHint: "",
    phaseHint: "",
    hydrated: false,
  };
}

function patchSession(sessionId: number, patch: Partial<SessionChatState>) {
  useStore.getState().patchSessionChat(sessionId, patch);
}

function voidOpenDrafts(messages: SessionChatState["messages"]) {
  return messages.map((message) => {
    const card = message.scheduleCard;
    if (!card || card.phase !== "draft" || card.mode === "suggest") return message;
    return { ...message, scheduleCard: { ...card, phase: "cancelled" } };
  });
}

function appendAssistantPlaceholder(sessionId: number, userText: string) {
  const prev = voidOpenDrafts(useStore.getState().sessionChats[sessionId]?.messages ?? []);
  patchSession(sessionId, {
    messages: [
      ...prev,
      { role: "user", content: userText },
      {
        role: "assistant",
        content: "",
        reasoning: null,
        fold: "hidden",
        citations: [],
        scheduleCard: null,
        artifact: null,
      },
    ],
    busy: true,
    thinking: true,
    thinkingSteps: [],
    routeHint: "",
    phaseHint: "正在连接模型…",
    hydrated: true,
  });
}

function prepareRegeneratePlaceholder(sessionId: number, assistantMessageId: number): boolean {
  const prev = useStore.getState().sessionChats[sessionId]?.messages ?? [];
  const idx = prev.findIndex((m) => m.id === assistantMessageId);
  if (idx < 0 || prev[idx]?.role !== "assistant") return false;
  patchSession(sessionId, {
    messages: [
      ...prev.slice(0, idx),
      {
        ...prev[idx],
        content: "",
        reasoning: null,
        fold: "hidden",
        citations: [],
        scheduleCard: null,
        artifact: null,
        feedback: null,
      },
    ],
    busy: true,
    thinking: true,
    thinkingSteps: [],
    routeHint: "",
    phaseHint: "正在连接模型…",
    hydrated: true,
  });
  return true;
}

function updateLastAssistant(
  sessionId: number,
  content: string,
  extras: {
    citations?: Citation[];
    scheduleCard?: ScheduleCard | null;
    artifact?: ArtifactPayload | null;
    reasoning?: string | null;
    fold?: ThinkingFold;
  }
) {
  const prev = useStore.getState().sessionChats[sessionId]?.messages ?? [];
  if (!prev.length) return;
  const copy = [...prev];
  const last = copy[copy.length - 1];
  copy[copy.length - 1] = {
    ...last,
    content,
    reasoning: extras.reasoning !== undefined ? extras.reasoning : last.reasoning,
    fold: extras.fold !== undefined ? extras.fold : last.fold,
    citations: extras.citations ?? last.citations,
    scheduleCard: extras.scheduleCard !== undefined ? extras.scheduleCard : last.scheduleCard,
    artifact: extras.artifact !== undefined ? extras.artifact : last.artifact,
  };
  patchSession(sessionId, { messages: copy });
}

async function runStreamLoop(
  sessionId: number,
  message: string,
  options: StartChatOptions & { regenerateMessageId?: number }
): Promise<void> {
  let acc = "";
  let cites: Citation[] = [];
  let scheduleCard: ScheduleCard | null = null;
  let msgArtifact: ArtifactPayload | null = null;
  const ac = new AbortController();
  abortControllers.set(sessionId, ac);
  let aborted = false;

  try {
    for await (const delta of streamChat(sessionId, message, {
      signal: ac.signal,
      useKb: options.useKb,
      documentIds: options.kbFocusIds.length ? options.kbFocusIds : undefined,
      regenerateMessageId: options.regenerateMessageId,
      onTitle: (title) => options.onTitle?.(sessionId, title),
      onAck: (path) => {
        patchSession(sessionId, {
          phaseHint: path === "fast" ? "正在连接模型…" : "正在规划…",
        });
      },
      onStatus: (phase) => {
        if (phase === "planning") {
          patchSession(sessionId, { phaseHint: "正在规划…" });
        }
      },
      onTtft: (ms, path) => {
        if (import.meta.env.DEV) {
          console.info(`[ttft] session=${sessionId} path=${path} ms=${ms}`);
        }
        patchSession(sessionId, { phaseHint: "" });
      },
      onIntent: (intent, forced) => {
        let routeHint = "";
        if (forced) routeHint = "强制知识库";
        else if (intent === "rag") routeHint = "已自动启用知识库";
        else if (intent === "web_search") routeHint = "联网搜索";
        else if (intent === "calendar") routeHint = "日程安排";
        else if (intent === "data_analysis") routeHint = "数据分析";
        else routeHint = "普通对话";
        patchSession(sessionId, { routeHint });
      },
      onThinking: (steps) => patchSession(sessionId, { thinkingSteps: steps }),
      onReasoning: (chunk) => {
        patchSession(sessionId, { thinking: false, phaseHint: "" });
        const prev = useStore.getState().sessionChats[sessionId]?.messages ?? [];
        const last = prev.at(-1);
        if (!last || last.role !== "assistant") return;
        const reasoning = `${last.reasoning || ""}${chunk}`;
        const fold = foldThinking(last.fold ?? "hidden", { type: "reasoning" });
        const copy = [...prev];
        copy[copy.length - 1] = { ...last, reasoning, fold };
        patchSession(sessionId, { messages: copy });
      },
      onCitations: (c) => {
        cites = c;
        updateLastAssistant(sessionId, acc, { citations: c });
      },
      onScheduleCard: (card) => {
        scheduleCard = card;
        updateLastAssistant(sessionId, acc, { scheduleCard: card });
      },
      onArtifact: (a) => {
        msgArtifact = a;
        const activeId = useStore.getState().activeId;
        if (activeId === sessionId) {
          useStore.getState().openArtifact(artifactFromPayload(a));
        }
        updateLastAssistant(sessionId, acc, { artifact: a });
      },
    })) {
      patchSession(sessionId, { thinking: false, phaseHint: "" });
      acc += delta;
      const prev = useStore.getState().sessionChats[sessionId]?.messages ?? [];
      const fold = foldThinking(prev.at(-1)?.fold ?? "hidden", { type: "answer" });
      updateLastAssistant(sessionId, acc, {
        citations: cites,
        scheduleCard,
        artifact: msgArtifact,
        fold,
      });
    }
  } catch (e) {
    if (isAbortError(e)) {
      aborted = true;
      if (!acc) {
        const prev = useStore.getState().sessionChats[sessionId]?.messages ?? [];
        if (prev.at(-1)?.role === "assistant" && !String(prev.at(-1)?.content || "").trim()) {
          patchSession(sessionId, { messages: prev.slice(0, -1) });
        }
      }
    } else {
      const msg = e instanceof Error ? e.message : "生成失败";
      updateLastAssistant(sessionId, `⚠️ ${msg}`, {});
    }
  } finally {
    abortControllers.delete(sessionId);
    patchSession(sessionId, { busy: false, thinking: false, phaseHint: "" });
    try {
      const before = useStore.getState().sessionChats[sessionId]?.messages ?? [];
      const synced = withHydratedFold(before, await loadMessages(sessionId));
      const messages = aborted ? synced : overlayUnsavedError(before, synced);
      patchSession(sessionId, { messages, hydrated: true });
    } catch {
      /* keep in-memory partial if sync fails */
    }
  }
}

/** Load history from API when session has no in-memory cache. */
export async function hydrateSessionMessages(sessionId: number) {
  const existing = useStore.getState().sessionChats[sessionId];
  if (existing?.hydrated || existing?.busy) return;
  const messages = withHydratedFold([], await loadMessages(sessionId));
  patchSession(sessionId, { messages, hydrated: true });
}

/** Start streaming; continues in background if user switches sessions. */
export async function startChatStream(
  sessionId: number,
  message: string,
  options: StartChatOptions
): Promise<void> {
  const state = useStore.getState();
  if (state.sessionChats[sessionId]?.busy) return;

  appendAssistantPlaceholder(sessionId, message);
  useStore.getState().touchSession(sessionId);
  await runStreamLoop(sessionId, message, options);
}

/** Regenerate an existing assistant reply (truncates later turns server-side). */
export async function startRegenerateStream(
  sessionId: number,
  assistantMessageId: number,
  options: StartChatOptions
): Promise<void> {
  const state = useStore.getState();
  if (state.sessionChats[sessionId]?.busy) return;
  if (!prepareRegeneratePlaceholder(sessionId, assistantMessageId)) return;
  useStore.getState().touchSession(sessionId);

  await runStreamLoop(sessionId, "", {
    ...options,
    regenerateMessageId: assistantMessageId,
  });
}

export function getSessionChatOrEmpty(sessionId: number): SessionChatState {
  return useStore.getState().sessionChats[sessionId] ?? emptyChatState();
}

export function isSessionBusy(sessionId: number): boolean {
  return Boolean(useStore.getState().sessionChats[sessionId]?.busy);
}
