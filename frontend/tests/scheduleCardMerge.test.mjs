import assert from "node:assert/strict";
import test from "node:test";

import {
  mergeStreamScheduleCard,
  overlaySettledScheduleCards,
} from "../src/utils/scheduleExpiry.ts";

const draft = {
  phase: "draft",
  mode: "update",
  title: "周会",
  confirm_token: "claim-1",
  expires_at: "2099-01-01T00:00:00.000Z",
};

test("a stream refresh keeps a confirmed card", () => {
  const confirmed = { ...draft, phase: "confirmed", event_id: 7 };
  assert.equal(mergeStreamScheduleCard(confirmed, draft), confirmed);
});

test("a stream refresh keeps a cancelled card", () => {
  const cancelled = { ...draft, phase: "cancelled" };
  assert.equal(mergeStreamScheduleCard(cancelled, draft), cancelled);
});

test("a stream refresh keeps an expired draft", () => {
  const expired = { ...draft, expires_at: "2020-01-01T00:00:00.000Z" };
  const extended = { ...draft, expires_at: "2099-01-01T00:00:00.000Z" };
  assert.equal(mergeStreamScheduleCard(expired, extended), expired);
});

test("a stream refresh still replaces a live draft", () => {
  const next = { ...draft, title: "改期周会" };
  assert.equal(mergeStreamScheduleCard(draft, next), next);
});

test("hydration keeps the confirmed card on the same claim", () => {
  const confirmed = { ...draft, phase: "confirmed", event_id: 7 };
  const merged = overlaySettledScheduleCards(
    [{ id: 3, scheduleCard: confirmed }],
    [{ id: 3, scheduleCard: draft, content: "请确认" }],
  );
  assert.equal(merged[0].scheduleCard, confirmed);
  assert.equal(merged[0].content, "请确认");
});
