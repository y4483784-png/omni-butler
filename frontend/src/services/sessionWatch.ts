import { loadSessionJobs, type SessionJobRow } from "../api/client";
import { useStore } from "../store/useStore";
import { reloadSessionMessages } from "./chatStream";

const controllers = new Map<number, AbortController>();

export type WatchDeps = {
  loadJobs: (sessionId: number, signal: AbortSignal) => Promise<{ cursor: number; jobs: SessionJobRow[] }>;
  reloadMessages: (sessionId: number) => Promise<void>;
  patchJobs: (sessionId: number, jobs: SessionJobRow[]) => void;
  fetchEvents: (sessionId: number, after: number, signal: AbortSignal) => Promise<Response>;
  visibility: () => string;
  listenVisibility: (onChange: () => void) => () => void;
  sleep: (ms: number, signal: AbortSignal) => Promise<void>;
};

function defaultDeps(): WatchDeps {
  return {
    loadJobs: (sessionId, signal) => loadSessionJobs(sessionId, signal),
    reloadMessages: (sessionId) => reloadSessionMessages(sessionId),
    patchJobs: (sessionId, jobs) => {
      useStore.getState().patchSessionChat(sessionId, { jobs });
    },
    fetchEvents: (sessionId, after, signal) =>
      fetch(`/api/sessions/${sessionId}/events?after=${after}`, { credentials: "include", signal }),
    visibility: () => document.visibilityState,
    listenVisibility: (onChange) => {
      document.addEventListener("visibilitychange", onChange);
      return () => document.removeEventListener("visibilitychange", onChange);
    },
    sleep: (ms, signal) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, ms);
        const onAbort = () => {
          clearTimeout(timer);
          reject(new DOMException("Aborted", "AbortError"));
        };
        if (signal.aborted) {
          onAbort();
          return;
        }
        signal.addEventListener("abort", onAbort, { once: true });
      }),
  };
}

export function watchSession(sessionId: number, deps?: WatchDeps): () => void {
  const io = deps ?? defaultDeps();
  controllers.get(sessionId)?.abort();
  const controller = new AbortController();
  controllers.set(sessionId, controller);
  const applied = new Set<number>();
  let cursor = 0;
  let events: AbortController | null = null;
  let failures = 0;
  const stopVisibility = io.listenVisibility(onVisible);

  const stop = () => {
    controller.abort();
    events?.abort();
    stopVisibility();
    if (controllers.get(sessionId) === controller) controllers.delete(sessionId);
  };

  function onVisible() {
    if (io.visibility() === "visible") void load();
  }

  async function load() {
    if (controller.signal.aborted) return;
    try {
      const snapshot = await io.loadJobs(sessionId, controller.signal);
      io.patchJobs(sessionId, snapshot.jobs);
      await io.reloadMessages(sessionId);
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
    const onAbort = () => next.abort();
    controller.signal.addEventListener("abort", onAbort, { once: true });
    void readEvents(sessionId, cursor, next.signal, io, async (seq) => {
      if (applied.has(seq)) return true;
      if (seq > cursor + 1) {
        next.abort();
        if (events === next) events = null;
        try {
          const snapshot = await io.loadJobs(sessionId, controller.signal);
          io.patchJobs(sessionId, snapshot.jobs);
          await io.reloadMessages(sessionId);
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
      failures = 0;
      const snapshot = await io.loadJobs(sessionId, controller.signal);
      io.patchJobs(sessionId, snapshot.jobs);
      await io.reloadMessages(sessionId);
      return true;
    }).then((ended) => {
      if (events !== next) return;
      events = null;
      if (ended === "abort" || controller.signal.aborted) return;
      void recover();
    });
  }

  async function recover() {
    const delay = Math.min(500, 50 * 2 ** failures);
    failures += 1;
    try {
      await io.sleep(delay, controller.signal);
    } catch {
      return;
    }
    if (controller.signal.aborted || events !== null) return;
    openEvents();
  }

  void load();
  return stop;
}

async function readEvents(
  sessionId: number,
  after: number,
  signal: AbortSignal,
  io: WatchDeps,
  accept: (seq: number) => Promise<boolean>
): Promise<"eof" | "error" | "abort" | "gap"> {
  if (signal.aborted) return "abort";
  let response: Response;
  try {
    response = await io.fetchEvents(sessionId, after, signal);
  } catch {
    return signal.aborted ? "abort" : "error";
  }
  if (!response.ok || response.body === null) return "error";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (!signal.aborted) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch {
      return signal.aborted ? "abort" : "error";
    }
    if (chunk.done) return "eof";
    buf += decoder.decode(chunk.value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      let payload: { seq?: number };
      try {
        payload = JSON.parse(line.slice(5).trim()) as { seq?: number };
      } catch {
        continue;
      }
      if (typeof payload.seq !== "number") continue;
      const keep = await accept(payload.seq);
      if (!keep) return "gap";
    }
  }
  return "abort";
}
