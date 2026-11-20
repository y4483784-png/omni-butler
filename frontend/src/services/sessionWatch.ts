import { loadSessionJobs } from "../api/client";
import { useStore } from "../store/useStore";
import { reloadSessionMessages } from "./chatStream";

const controllers = new Map<number, AbortController>();

export function watchSession(sessionId: number): () => void {
  controllers.get(sessionId)?.abort();
  const controller = new AbortController();
  controllers.set(sessionId, controller);
  const applied = new Set<number>();
  let cursor = 0;
  let events: AbortController | null = null;

  const stop = () => {
    controller.abort();
    events?.abort();
    document.removeEventListener("visibilitychange", onVisible);
    if (controllers.get(sessionId) === controller) controllers.delete(sessionId);
  };

  const onVisible = () => {
    if (document.visibilityState === "visible") void load();
  };

  async function load() {
    if (controller.signal.aborted) return;
    try {
      const snapshot = await loadSessionJobs(sessionId, controller.signal);
      useStore.getState().patchSessionChat(sessionId, { jobs: snapshot.jobs });
      await reloadSessionMessages(sessionId);
      if (snapshot.cursor === cursor && events !== null) return;
      cursor = snapshot.cursor;
      openEvents();
    } catch {
      if (controller.signal.aborted) return;
    }
  }

  function openEvents() {
    events?.abort();
    const next = new AbortController();
    events = next;
    controller.signal.addEventListener("abort", () => next.abort(), { once: true });
    void readEvents(sessionId, cursor, next.signal, async (seq) => {
      if (applied.has(seq)) return true;
      if (seq > cursor + 1) {
        next.abort();
        if (events === next) events = null;
        try {
          const snapshot = await loadSessionJobs(sessionId, controller.signal);
          useStore.getState().patchSessionChat(sessionId, { jobs: snapshot.jobs });
          await reloadSessionMessages(sessionId);
          if (snapshot.cursor > cursor) {
            cursor = snapshot.cursor;
            openEvents();
          }
        } catch {
          return false;
        }
        return false;
      }
      applied.add(seq);
      cursor = seq;
      await reloadSessionMessages(sessionId);
      return true;
    });
  }

  document.addEventListener("visibilitychange", onVisible);
  void load();
  return stop;
}

async function readEvents(
  sessionId: number,
  after: number,
  signal: AbortSignal,
  accept: (seq: number) => Promise<boolean>
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`/api/sessions/${sessionId}/events?after=${after}`, {
      credentials: "include",
      signal,
    });
  } catch {
    return;
  }
  if (!response.ok || response.body === null) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (!signal.aborted) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch {
      return;
    }
    if (chunk.done) return;
    buf += decoder.decode(chunk.value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const payload = JSON.parse(line.slice(5).trim()) as { seq?: number };
      if (typeof payload.seq !== "number") continue;
      const keep = await accept(payload.seq);
      if (!keep) return;
    }
  }
}
