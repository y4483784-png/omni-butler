import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("errand confirm button commits and does not render a secret", () => {
  const source = readFileSync(new URL("../src/components/ErrandCard.tsx", import.meta.url), "utf8");
  const confirmAt = source.indexOf("async function confirm");
  const labelAt = source.indexOf("确认下单");
  assert.ok(confirmAt >= 0 && labelAt > confirmAt);
  assert.match(source.slice(confirmAt, labelAt), /commitErrand/);
  const button = source.slice(source.lastIndexOf("<button", labelAt), source.indexOf("</button>", labelAt));
  assert.match(button, /onClick=\{\(\) => void confirm\(\)\}/);
  assert.match(source, /merchant_key/);
  assert.match(source, /pay_method/);
  assert.match(source, /amount_cap/);
  assert.match(source, /address/);
  assert.doesNotMatch(source, /password/i);
  assert.doesNotMatch(source, /cvv/i);
  assert.doesNotMatch(source, /type="password"/);
  assert.doesNotMatch(source, /4111111111111111/);
  assert.doesNotMatch(source, /\bpan\b/i);
});

test("chat renders an errand card and does not hand it to the schedule card", () => {
  const source = readFileSync(new URL("../src/components/ChatWindow.tsx", import.meta.url), "utf8");
  assert.match(source, /mode === "errand"/);
  assert.match(source, /<ErrandCard card=\{m\.scheduleCard\} onChange=\{onScheduleChange\} \/>/);
  const lowerAt = source.lastIndexOf("<ScheduleCard");
  const gate = source.slice(Math.max(0, lowerAt - 280), lowerAt);
  assert.match(gate, /!== "errand"/);
});
