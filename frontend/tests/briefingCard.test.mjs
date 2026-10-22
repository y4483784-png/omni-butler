import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("meeting brief card does not write or invent a confirm action", () => {
  const source = readFileSync(new URL("../src/components/MeetingBriefCard.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /确认/);
  assert.doesNotMatch(source, /rsvp/i);
  assert.doesNotMatch(source, /fetch\(/);
  assert.doesNotMatch(source, /\/api\/calendar/);
});

test("schedule card returns null before a confirm label for a non-write mode", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  const guardAt = source.indexOf("const writeModes");
  const confirmAt = source.indexOf("const confirmLabel");
  assert.ok(guardAt >= 0 && confirmAt > guardAt);
  assert.match(source.slice(guardAt, confirmAt), /return null/);
});

test("chat renders the brief above the markdown answer", () => {
  const source = readFileSync(new URL("../src/components/ChatWindow.tsx", import.meta.url), "utf8");
  const briefAt = source.indexOf('mode === "brief"');
  const markdownAt = source.indexOf("<Markdown");
  assert.ok(briefAt >= 0 && markdownAt > briefAt);
  const lowerAt = source.lastIndexOf("<ScheduleCard");
  const lower = source.slice(lowerAt - 80, lowerAt);
  assert.match(lower, /mode !== "brief"/);
});
