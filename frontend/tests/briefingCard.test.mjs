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

test("old stored brief still has a mail section and an empty docs section", async () => {
  const { briefFromWire } = await import("../src/utils/briefFromWire.ts");
  const card = briefFromWire({
    mode: "brief",
    phase: "info",
    status: "ready",
    meeting: {
      title: "客户会",
      start_at: "2026-10-19T10:00:00",
      end_at: "",
      attendees: ["li@acme.com"],
    },
    emails: [
      {
        message_id: "m1",
        thread_id: "t1",
        sender: "A",
        sent_at: "",
        subject: "议程",
        sentences: ["请确认议程。"],
        url: "https://mail.google.com/mail/u/0/#all/t1",
      },
    ],
    focus: [{ message_id: "m1", sentence_index: 0 }],
  });
  assert.equal(card.mail.state, "found");
  assert.equal(card.mail.emails[0].subject, "议程");
  assert.equal(card.docs.state, "empty");
  assert.deepEqual(card.meeting.attendees[0], { email: "li@acme.com", name: "" });
  assert.equal(card.status, "ready");
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
