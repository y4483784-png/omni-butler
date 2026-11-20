import { loadSessionJobs, type ChatMsg, type SessionJobRow } from "../api/client";
import { useStore } from "../store/useStore";
import { applySessionMessages, readSessionMessages } from "./chatStream";

const controllers = new Map<number, AbortController>();

export type WatchDeps = {
  loadJobs: (sessionId: number, signal: AbortSignal) => Promise<{ cursor: number; jobs: SessionJobRow[] }>;
  readMessages: (sessionId: number) => Promise<ChatMsg[]>;
  applyMessages: (sessionId: number, messages: ChatMsg[]) => void;
  patchJobs: (sessionId: number, jobs: SessionJobRow[]) => void;
  fetchEvents: (sessionId: number, after: number, signal: AbortSignal) => Promise<Response>;
  visibility: () => string;
  listenVisibility: (onChange: () => void) => () => void;
  sleep: (ms: number, signal: AbortSignal) => Promise<void>;
};

function defaultDeps(): WatchDeps {
  return {
    loadJobs: (sessionId, signal) => loadSessionJobs(sessionId, signal),
    readMessages: (sessionId) => readSessionMessages(sessionId),
    applyMessages: (sessionId, messages) => applySessionMessages(sessionId, messages),
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
  let loadEpoch = 0;
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

  async function hydrate(ticket: number) {
    const snapshot = await io.loadJobs(sessionId, controller.signal);
    const messages = await io.readMessages(sessionId);
    return { ticket, snapshot, messages };
  }

  function takeSnapshot(
    ticket: number,
    snapshot: { cursor: number; jobs: Parameters<WatchDeps["patchJobs"]>[1] },
    messages: ChatMsg[],
  ) {
    if (controller.signal.aborted) return false;
    if (snapshot.cursor < cursor) return false;
    if (ticket !== loadEpoch && snapshot.cursor <= cursor) return false;
    io.patchJobs(sessionId, snapshot.jobs);
    io.applyMessages(sessionId, messages);
    cursor = Math.max(cursor, snapshot.cursor);
    return true;
  }

  async function load() {
    if (controller.signal.aborted) return;
    const ticket = ++loadEpoch;
    try {
      const loaded = await hydrate(ticket);
      if (!takeSnapshot(loaded.ticket, loaded.snapshot, loaded.messages)) return;
      if (loaded.snapshot.cursor === cursor && events !== null && loaded.ticket !== loadEpoch) return;
      if (events !== null && loaded.snapshot.cursor === cursor) return;
      openEvents();
    } catch {
      if (controller.signal.aborted) return;
      void recover();
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
        const ticket = ++loadEpoch;
        const loaded = await hydrate(ticket);
        if (controller.signal.aborted) return false;
        next.abort();
        if (events === next) events = null;
        if (loaded.snapshot.cursor < cursor) return false;
        if (!takeSnapshot(loaded.ticket, loaded.snapshot, loaded.messages)) return false;
        openEvents();
        return false;
      }
      const ticket = ++loadEpoch;
      const loaded = await hydrate(ticket);
      if (controller.signal.aborted) return false;
      if (loaded.snapshot.cursor < seq && cursor < seq) return false;
      if (cursor >= seq && loaded.snapshot.cursor < cursor) {
        applied.add(seq);
        failures = 0;
        return true;
      }
      if (!takeSnapshot(loaded.ticket, loaded.snapshot, loaded.messages)) return cursor >= seq;
      applied.add(seq);
      cursor = Math.max(cursor, seq);
      failures = 0;
      return true;
    })
      .then((ended) => {
        if (events !== next) return;
        events = null;
        if (ended === "abort" || controller.signal.aborted) return;
        void recover();
      })
      .catch(() => {
        if (events !== next) return;
        events = null;
        if (controller.signal.aborted) return;
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
      let keep = false;
      try {
        keep = await accept(payload.seq);
      } catch {
        return signal.aborted ? "abort" : "error";
      }
      if (!keep) return "gap";
    }
  }
  return "abort";
}
