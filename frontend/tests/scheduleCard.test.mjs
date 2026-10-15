import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("cancel card title is editable", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  const titleField = source.slice(source.indexOf(">标题"), source.indexOf(">开始"));
  const startField = source.slice(source.indexOf(">开始"), source.indexOf(">结束"));
  assert.match(titleField, /onChange=\{\(event\) => setTitle\(event\.target\.value\)\}/);
  assert.match(titleField, /disabled=\{locked\}/);
  assert.doesNotMatch(titleField, /mode === "cancel"/);
  assert.match(startField, /disabled=\{locked \|\| mode === "cancel"\}/);
});
