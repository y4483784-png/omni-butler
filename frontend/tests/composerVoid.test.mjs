import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register(new URL("./resolve-ts.mjs", import.meta.url), import.meta.url);

const { useStore } = await import("../src/store/useStore.ts");
const { startChatStream } = await import("../src/services/chatStream.ts");

const sessionId = 9;
const keepTaskRunId = 2;

function draft(id, title, token, extra = {}) {
  const scheduleCard = {
    phase: "draft",
    mode: extra.mode ?? "create",
    title,
    confirm_token: token,
  };
  if (extra.task_kind) scheduleCard.task_kind = extra.task_kind;
  return { id, role: "assistant", content: title, scheduleCard };
}

const before = [
  draft(11, "stream card", "keep-token"),
  draft(12, "waiting card", "wait-token", { task_kind: "job" }),
  draft(13, "running card", "other-token", { task_kind: "job" }),
  draft(14, "suggest card", "loose-token", { mode: "suggest" }),
  draft(15, "turn card", "turn-token"),
];

function jobs() {
  return [
    {
      id: 1,
      status: "waiting_person",
      kind: "job",
      goal: "wait",
      generation: 4,
      confirm_tokens: ["wait-token"],
    },
    {
      id: keepTaskRunId,
      status: "running",
      kind: "job",
      goal: "keep",
      generation: 5,
      confirm_tokens: ["keep-token"],
    },
    {
      id: 3,
      status: "running",
      kind: "job",
      goal: "other",
      generation: 6,
      confirm_tokens: ["other-token"],
    },
  ];
}

function phase(title) {
  const row = useStore
    .getState()
    .sessionChats[sessionId].messages.find((message) => message.scheduleCard?.title === title);
  return row?.scheduleCard?.phase;
}

test("a send keeps the same drafts the server void keeps", async () => {
  const originalFetch = globalThis.fetch;
  useStore.setState({
    sessions: [],
    activeId: sessionId,
    sessionChats: {
      [sessionId]: {
        messages: before.map((row) => ({ ...row, scheduleCard: { ...row.scheduleCard } })),
        busy: false,
        thinking: false,
        thinkingSteps: [],
        routeHint: "",
        phaseHint: "",
        jobs: jobs(),
        hydrated: true,
      },
    },
  });
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.includes("/api/chat")) {
      const body = [
        `data: ${JSON.stringify({ type: "ack", path: "agent", keep_task_run_id: keepTaskRunId })}\n\n`,
        `data: ${JSON.stringify({ type: "done" })}\n\n`,
      ].join("");
      return new Response(body, { status: 200 });
    }
    if (url.includes(`/api/sessions/${sessionId}/messages`)) {
      return Response.json(before.map((row) => ({ ...row, scheduleCard: { ...row.scheduleCard } })));
    }
    return new Response("missing", { status: 404 });
  };
  try {
    const result = await startChatStream(sessionId, "next", { useKb: false, kbFocusIds: [] });
    assert.equal(result.accepted, true);
    assert.equal(phase("stream card"), "draft");
    assert.equal(phase("waiting card"), "draft");
    assert.equal(phase("running card"), "cancelled");
    assert.equal(phase("suggest card"), "draft");
    assert.equal(phase("turn card"), "cancelled");
    assert.equal(before[0].scheduleCard.task_kind, undefined);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
