import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("a session loads jobs on visibility and applies each event seq once", () => {
  const source = readFileSync(new URL("../src/services/sessionWatch.ts", import.meta.url), "utf8");
  const window = readFileSync(new URL("../src/components/ChatWindow.tsx", import.meta.url), "utf8");
  assert.match(source, /loadSessionJobs/);
  assert.match(source, /visibilitychange/);
  assert.match(source, /document\.visibilityState === "visible"/);
  assert.match(source, /events\?after=/);
  assert.match(source, /new Set<number>/);
  assert.match(source, /applied\.has\(seq\)/);
  assert.doesNotMatch(source, /setInterval/);
  assert.match(window, /watchSession\(sessionId\)/);
  assert.match(window, /job\.status === "running"/);
  assert.match(window, /job\.status === "waiting_person"/);
});
