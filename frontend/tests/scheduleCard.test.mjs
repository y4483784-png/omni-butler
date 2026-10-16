import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("cancel card title is editable", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  const titleAt = source.indexOf("\n            标题\n");
  const startAt = source.indexOf("\n            开始\n");
  const endAt = source.indexOf("结束\n");
  const titleField = source.slice(titleAt, startAt);
  const startField = source.slice(startAt, endAt);
  assert.match(titleField, /onChange=\{\(event\) => setTitle\(event\.target\.value\)\}/);
  assert.match(titleField, /disabled=\{locked\}/);
  assert.doesNotMatch(titleField, /mode === "cancel"/);
  assert.match(startField, /disabled=\{locked \|\| mode === "cancel"\}/);
});

test("list card is time and title without a confirm button", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  const listAt = source.indexOf('if (mode === "list")');
  const suggestAt = source.indexOf('if (mode === "suggest")');
  const branch = source.slice(listAt, suggestAt);
  assert.match(branch, /formatListWhen/);
  assert.match(branch, /item\.title/);
  assert.doesNotMatch(branch, /确认/);
  assert.doesNotMatch(branch, /参与人/);
});

test("block form shows duration and hides participants", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  assert.match(source, /card\.kind === "block" && \(mode === "create" \|\| mode === "update"\)/);
  const blockAt = source.indexOf("{blockForm ? (");
  const elseAt = source.indexOf(") : (", blockAt);
  const blockBranch = source.slice(blockAt, elseAt);
  assert.match(blockBranch, /时长/);
  assert.doesNotMatch(blockBranch, /参与人/);
  assert.match(source, /说明/);
  const cancelAt = source.indexOf('action: "cancel"');
  const updateAt = source.indexOf('action: "update"', cancelAt);
  assert.doesNotMatch(source.slice(cancelAt, updateAt), /description/);
});

test("google confirm submits the card external id", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  const cancelAt = source.indexOf('action: "cancel"');
  const updateAt = source.indexOf('action: "update"', cancelAt);
  const cancelPayload = source.slice(cancelAt, updateAt);
  const updatePayload = source.slice(updateAt, source.indexOf("});", updateAt));
  assert.match(cancelPayload, /external_id: \(card\.external_id \|\| ""\)\.trim\(\)/);
  assert.match(updatePayload, /external_id: \(card\.external_id \|\| ""\)\.trim\(\)/);
  assert.doesNotMatch(cancelPayload, /description/);
});

test("list card renders above the advice text", () => {
  const source = readFileSync(new URL("../src/components/ChatWindow.tsx", import.meta.url), "utf8");
  const listAt = source.indexOf('m.scheduleCard?.mode === "list"');
  const textAt = source.indexOf("<Markdown");
  const otherAt = source.indexOf('m.scheduleCard.mode !== "list"');
  assert.ok(listAt !== -1 && textAt !== -1 && otherAt !== -1);
  assert.ok(listAt < textAt);
  assert.ok(textAt < otherAt);
});
