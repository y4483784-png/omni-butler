import { streamChat, loadMessages } from "../api/client";
import type { Citation, ScheduleCard, ArtifactPayload } from "../api/client";
import { useStore, type SessionChatState } from "../store/useStore";
import { artifactFromPayload } from "../utils/artifact";

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

function appendAssistantPlaceholder(sessionId: number, userText: string) {
  const prev = useStore.getState().sessionChats[sessionId]?.messages ?? [];
  patchSession(sessionId, {
    messages: [
      ...prev,
      { role: "user", content: userText },
      { role: "assistant", content: "", citations: [], scheduleCard: null, artifact: null },
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
  }
) {
  const prev = useStore.getState().sessionChats[sessionId]?.messages ?? [];
  if (!prev.length) return;
  const copy = [...prev];
  const last = copy[copy.length - 1];
  copy[copy.length - 1] = {
    ...last,
    content,
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
      updateLastAssistant(sessionId, acc, {
        citations: cites,
        scheduleCard,
        artifact: msgArtifact,
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
      const synced = await loadMessages(sessionId);
      const beforeLast = before.at(-1);
      const syncedHasAssistantError = synced.some(
        (m) =>
          m.role === "assistant" &&
          (m.content.includes("模型调用失败") || m.content.startsWith("⚠️"))
      );
      const keepUiError =
        !aborted &&
        beforeLast?.role === "assistant" &&
        String(beforeLast.content || "").startsWith("⚠️") &&
        !syncedHasAssistantError;
      if (keepUiError) {
        const merged = [...synced];
        if (merged.at(-1)?.role !== "assistant") {
          merged.push({
            role: "assistant",
            content: beforeLast.content,
            citations: [],
            scheduleCard: null,
            artifact: null,
          });
        } else {
          merged[merged.length - 1] = {
            ...merged[merged.length - 1],
            content: beforeLast.content,
          };
        }
        patchSession(sessionId, { messages: merged, hydrated: true });
      } else {
        patchSession(sessionId, { messages: synced, hydrated: true });
      }
    } catch {
      /* keep in-memory partial if sync fails */
    }
  }
}

/** Load history from API when session has no in-memory cache. */
export async function hydrateSessionMessages(sessionId: number) {
  const existing = useStore.getState().sessionChats[sessionId];
  if (existing?.hydrated || existing?.busy) return;
  const messages = await loadMessages(sessionId);
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
