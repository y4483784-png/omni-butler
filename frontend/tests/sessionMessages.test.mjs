import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register(new URL("./resolve-ts.mjs", import.meta.url), import.meta.url);

const revoked = [];
const nativeRevoke = URL.revokeObjectURL?.bind(URL);

function watchRevoke() {
  URL.revokeObjectURL = (url) => {
    revoked.push(String(url));
    if (!nativeRevoke) return;
    try {
      nativeRevoke(url);
    } catch {
      /* node has no blob registry */
    }
  };
}

function restoreRevoke() {
  if (nativeRevoke) URL.revokeObjectURL = nativeRevoke;
}

const { watchSession } = await import("../src/services/sessionWatch.ts");
const { useStore } = await import("../src/store/useStore.ts");
const { applySessionMessages } = await import("../src/services/chatStream.ts");

function empty(messages) {
  return {
    messages,
    busy: false,
    thinking: false,
    thinkingSteps: [],
    routeHint: "",
    phaseHint: "",
    jobs: [],
    hydrated: true,
  };
}

function card(phase, token) {
  return {
    id: 1,
    role: "assistant",
    content: phase,
    scheduleCard: {
      phase,
      mode: "create",
      title: phase,
      confirm_token: token,
      confirm_label: "确认",
    },
  };
}

function hang() {
  return new Promise(() => {});
}

test("a delayed older message load does not rewind a newer card or release its preview", async () => {
  watchRevoke();
  revoked.length = 0;
  const kept = [{ mediaType: "image/png", url: "blob:kept" }];
  const fresh = [
    { ...card("confirmed", "tok"), localImages: kept },
    { id: 2, role: "user", content: "later" },
  ];
  useStore.setState({ sessionChats: { 41: empty(fresh) } });
  let release = () => {};
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let reads = 0;
  let show = () => {};
  const stop = watchSession(41, {
    loadJobs: async () => {
      const older = reads > 0;
      return {
        cursor: older ? 1 : 2,
        jobs: [{ id: 9, status: older ? "running" : "completed", goal: older ? "old" : "new", generation: 1, kind: "job" }],
      };
    },
    readMessages: async () => {
      reads += 1;
      if (reads === 1) return fresh.map((row) => ({ ...row }));
      await gate;
      return [card("draft", "tok")];
    },
    applyMessages: (sessionId, messages) => applySessionMessages(sessionId, messages),
    patchJobs: (sessionId, jobs) => useStore.getState().patchSessionChat(sessionId, { jobs }),
    fetchEvents: async () => hang(),
    visibility: () => "visible",
    listenVisibility: (fn) => {
      show = fn;
      return () => {};
    },
    sleep: () => hang(),
  });
  await new Promise((resolve) => setTimeout(resolve, 30));
  show();
  await new Promise((resolve) => setTimeout(resolve, 20));
  release();
  await new Promise((resolve) => setTimeout(resolve, 30));
  const stored = useStore.getState().sessionChats[41];
  assert.equal(stored.messages[0].scheduleCard.phase, "confirmed");
  assert.equal(stored.messages[1].content, "later");
  assert.equal(stored.messages[0].localImages[0].url, "blob:kept");
  assert.equal(stored.jobs[0].status, "completed");
  assert.equal(revoked.includes("blob:kept"), false);
  stop();
  restoreRevoke();
});

test("stop during a pending message load does not mutate the store or revoke its preview", async () => {
  watchRevoke();
  revoked.length = 0;
  const preview = [{ mediaType: "image/png", url: "blob:pending" }];
  useStore.setState({
    sessionChats: { 42: empty([{ ...card("draft", "wait"), localImages: preview }]) },
  });
  let release = () => {};
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const stop = watchSession(42, {
    loadJobs: async () => ({ cursor: 1, jobs: [] }),
    readMessages: async () => {
      await gate;
      return [card("confirmed", "wait")];
    },
    applyMessages: (sessionId, messages) => applySessionMessages(sessionId, messages),
    patchJobs: (sessionId, jobs) => useStore.getState().patchSessionChat(sessionId, { jobs }),
    fetchEvents: async () => hang(),
    visibility: () => "hidden",
    listenVisibility: () => () => {},
    sleep: () => hang(),
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  stop();
  release();
  await new Promise((resolve) => setTimeout(resolve, 30));
  const stored = useStore.getState().sessionChats[42];
  assert.equal(stored.messages[0].scheduleCard.phase, "draft");
  assert.equal(stored.messages[0].localImages[0].url, "blob:pending");
  assert.equal(revoked.includes("blob:pending"), false);
  restoreRevoke();
});

test("a failed message read leaves the card and the preview in place", async () => {
  watchRevoke();
  revoked.length = 0;
  useStore.setState({
    sessionChats: {
      43: empty([{ ...card("draft", "stay"), localImages: [{ mediaType: "image/png", url: "blob:stay" }] }]),
    },
  });
  const stop = watchSession(43, {
    loadJobs: async () => ({ cursor: 1, jobs: [] }),
    readMessages: async () => {
      throw new Error("messages down");
    },
    applyMessages: (sessionId, messages) => applySessionMessages(sessionId, messages),
    patchJobs: (sessionId, jobs) => useStore.getState().patchSessionChat(sessionId, { jobs }),
    fetchEvents: async () => hang(),
    visibility: () => "hidden",
    listenVisibility: () => () => {},
    sleep: () => hang(),
  });
  await new Promise((resolve) => setTimeout(resolve, 30));
  const stored = useStore.getState().sessionChats[43];
  assert.equal(stored.messages[0].scheduleCard.phase, "draft");
  assert.equal(revoked.includes("blob:stay"), false);
  stop();
  restoreRevoke();
});

test("an accepted load merges the latest preview and releases only a dropped one", async () => {
  watchRevoke();
  revoked.length = 0;
  useStore.setState({
    sessionChats: {
      44: empty([
        { id: 1, role: "user", content: "gone", localImages: [{ mediaType: "image/png", url: "blob:gone" }] },
        { id: 2, role: "user", content: "stay" },
      ]),
    },
  });
  const stop = watchSession(44, {
    loadJobs: async () => ({
      cursor: 3,
      jobs: [{ id: 4, status: "running", goal: "live", generation: 1, kind: "job" }],
    }),
    readMessages: async () => {
      useStore.getState().patchSessionChat(44, {
        messages: [
          { id: 1, role: "user", content: "gone", localImages: [{ mediaType: "image/png", url: "blob:gone" }] },
          { id: 2, role: "user", content: "stay", localImages: [{ mediaType: "image/png", url: "blob:stay" }] },
        ],
      });
      return [{ id: 2, role: "user", content: "stay" }];
    },
    applyMessages: (sessionId, messages) => applySessionMessages(sessionId, messages),
    patchJobs: (sessionId, jobs) => useStore.getState().patchSessionChat(sessionId, { jobs }),
    fetchEvents: async () => hang(),
    visibility: () => "hidden",
    listenVisibility: () => () => {},
    sleep: () => hang(),
  });
  await new Promise((resolve) => setTimeout(resolve, 40));
  const stored = useStore.getState().sessionChats[44];
  assert.equal(stored.jobs[0].status, "running");
  assert.equal(stored.messages[0].localImages[0].url, "blob:stay");
  assert.equal(revoked.includes("blob:stay"), false);
  assert.equal(revoked.includes("blob:gone"), true);
  stop();
  restoreRevoke();
});
