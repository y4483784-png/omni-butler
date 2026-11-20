import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register(new URL("./resolve-ts.mjs", import.meta.url), import.meta.url);

const { watchSession } = await import("../src/services/sessionWatch.ts");

function streamOf(chunks) {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      controller.close();
    },
  });
}

function hangingStream() {
  return new ReadableStream({
    start() {},
  });
}

function depsFor(handlers) {
  const jobs = new Map();
  const patches = [];
  const messages = [];
  const fetches = [];
  let visible = "visible";
  let onVisible = () => {};
  const sleeps = [];
  return {
    jobs,
    patches,
    messages,
    fetches,
    sleeps,
    show() {
      visible = "visible";
      onVisible();
    },
    io: {
      loadJobs: async (sessionId) => {
        const row = jobs.get(sessionId) ?? { cursor: 0, jobs: [] };
        return { cursor: row.cursor, jobs: row.jobs.map((job) => ({ ...job })) };
      },
      reloadMessages: async (sessionId) => {
        messages.push(sessionId);
      },
      patchJobs: (sessionId, next) => {
        patches.push({ sessionId, jobs: next });
      },
      fetchEvents: async (sessionId, after, signal) => {
        fetches.push({ sessionId, after });
        const response = await handlers.next(sessionId, after, signal);
        return response;
      },
      visibility: () => visible,
      listenVisibility: (fn) => {
        onVisible = fn;
        return () => {
          onVisible = () => {};
        };
      },
      sleep: (ms, signal) => {
        sleeps.push(ms);
        return new Promise((resolve, reject) => {
          if (signal.aborted) {
            reject(new DOMException("Aborted", "AbortError"));
            return;
          }
          const timer = setTimeout(resolve, ms);
          signal.addEventListener(
            "abort",
            () => {
              clearTimeout(timer);
              reject(new DOMException("Aborted", "AbortError"));
            },
            { once: true }
          );
        });
      },
    },
  };
}

function ok(body) {
  return new Response(streamOf([body]), { status: 200 });
}

test("a status event patches that session's jobs once and a repeat does not", async () => {
  const harness = depsFor({
    next: async () =>
      ok('data: {"seq":1,"kind":"status","body":"running"}\n\ndata: {"seq":1,"kind":"status","body":"running"}\n\n'),
  });
  harness.jobs.set(7, {
    cursor: 0,
    jobs: [{ id: 3, status: "running", goal: "alpha", generation: 1, kind: "job" }],
  });
  const stop = watchSession(7, harness.io);
  try {
    await new Promise((resolve) => setTimeout(resolve, 40));
    const own = harness.patches.filter((row) => row.sessionId === 7);
    assert.equal(own.length, 2);
    assert.deepEqual(own[1].jobs, harness.jobs.get(7).jobs);
  } finally {
    stop();
  }
});

test("two sessions apply their own events", async () => {
  const harness = depsFor({
    next: async (sessionId, after) => {
      if (after > 0) return ok("");
      const body = sessionId === 1 ? "one" : "two";
      return ok(`data: {"seq":1,"kind":"status","body":"${body}"}\n\n`);
    },
  });
  harness.jobs.set(1, { cursor: 0, jobs: [{ id: 11, status: "running", goal: "one", generation: 1, kind: "job" }] });
  harness.jobs.set(2, { cursor: 0, jobs: [{ id: 22, status: "waiting_person", goal: "two", generation: 1, kind: "job" }] });
  const stop1 = watchSession(1, harness.io);
  const stop2 = watchSession(2, harness.io);
  await new Promise((resolve) => setTimeout(resolve, 40));
  const first = harness.patches.filter((row) => row.sessionId === 1).at(-1);
  const second = harness.patches.filter((row) => row.sessionId === 2).at(-1);
  assert.equal(first.jobs[0].id, 11);
  assert.equal(second.jobs[0].id, 22);
  assert.equal(second.jobs[0].status, "waiting_person");
  stop1();
  stop2();
});

test("EOF and a failed fetch reconnect from the last applied seq", async () => {
  let calls = 0;
  const harness = depsFor({
    next: async (_sessionId, after) => {
      calls += 1;
      if (calls === 1) return ok('data: {"seq":1,"kind":"status","body":"running"}\n\n');
      if (calls === 2) throw new Error("network");
      if (after === 1) return ok('data: {"seq":2,"kind":"status","body":"waiting_person"}\n\n');
      return new Response(hangingStream(), { status: 200 });
    },
  });
  harness.jobs.set(4, {
    cursor: 0,
    jobs: [{ id: 9, status: "running", goal: "stay", generation: 1, kind: "job" }],
  });
  const stop = watchSession(4, harness.io);
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.ok(harness.fetches.some((row) => row.after === 1));
  assert.ok(harness.sleeps.length >= 1);
  stop();
});

test("abort during reconnect does not open another stream", async () => {
  const harness = depsFor({
    next: async () => {
      throw new Error("down");
    },
  });
  harness.jobs.set(5, { cursor: 0, jobs: [] });
  const stop = watchSession(5, harness.io);
  await new Promise((resolve) => setTimeout(resolve, 20));
  const before = harness.fetches.length;
  stop();
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(harness.fetches.length, before);
});

test("visibility reopens a dead stream and a live stream stays open", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let opened = 0;
  const harness = depsFor({
    next: async () => {
      opened += 1;
      if (opened === 1) throw new Error("eof");
      await gate;
      return new Response(hangingStream(), { status: 200 });
    },
  });
  harness.jobs.set(6, { cursor: 4, jobs: [{ id: 1, status: "running", goal: "g", generation: 1, kind: "job" }] });
  const stop = watchSession(6, harness.io);
  await new Promise((resolve) => setTimeout(resolve, 20));
  harness.show();
  await new Promise((resolve) => setTimeout(resolve, 40));
  const afterFirstShow = harness.fetches.length;
  harness.show();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(harness.fetches.length, afterFirstShow);
  release();
  stop();
});

test("a gap reloads the snapshot and continues after the snapshot cursor", async () => {
  let loads = 0;
  const harness = depsFor({
    next: async (_sessionId, after) => {
      if (after === 0) return ok('data: {"seq":3,"kind":"status","body":"running"}\n\n');
      return new Response(hangingStream(), { status: 200 });
    },
  });
  harness.io.loadJobs = async () => {
    loads += 1;
    if (loads === 1) {
      return { cursor: 0, jobs: [{ id: 2, status: "running", goal: "old", generation: 1, kind: "job" }] };
    }
    return { cursor: 3, jobs: [{ id: 2, status: "running", goal: "gap", generation: 1, kind: "job" }] };
  };
  const stop = watchSession(8, harness.io);
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.ok(harness.fetches.some((row) => row.after === 3));
  assert.ok(harness.patches.some((row) => row.jobs[0] && row.jobs[0].goal === "gap"));
  stop();
});
