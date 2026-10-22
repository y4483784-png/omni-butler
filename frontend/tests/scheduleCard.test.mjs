import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("cancel card title is read only", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  const titleAt = source.indexOf("\n            标题\n");
  const startAt = source.indexOf("\n            开始\n");
  const endAt = source.indexOf("结束\n");
  const titleField = source.slice(titleAt, startAt);
  const startField = source.slice(startAt, endAt);
  assert.match(titleField, /onChange=\{\(event\) => setTitle\(event\.target\.value\)\}/);
  assert.match(titleField, /disabled=\{locked \|\| mode === "cancel"\}/);
  assert.match(startField, /disabled=\{locked \|\| mode === "cancel"\}/);
  assert.match(source, /待确认删除/);
  assert.match(source, /确认删除/);
  assert.match(source, /先不删/);
  assert.match(source, /phase: "cancelled"/);
  assert.doesNotMatch(source, /确认撤销/);
  assert.doesNotMatch(source, /待确认撤销/);
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
  const guardAt = source.indexOf('const externalId = (card.external_id || "").trim();');
  const cancelAt = source.indexOf('action: "cancel"');
  const updateAt = source.indexOf('action: "update"', cancelAt);
  const guard = source.slice(guardAt, source.indexOf("if (mode === \"cancel\")", updateAt));
  const cancelPayload = source.slice(cancelAt, updateAt);
  const updatePayload = source.slice(updateAt, source.indexOf("});", updateAt));
  assert.match(guard, /!externalId && id == null/);
  assert.match(cancelPayload, /external_id: externalId/);
  assert.match(updatePayload, /external_id: externalId/);
  assert.match(cancelPayload, /participants: \[\]/);
  assert.doesNotMatch(cancelPayload, /description/);
});

test("non-block form adds guests beside the schedule form", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  const formAt = source.indexOf('className="schedule-form"');
  const formEnd = source.indexOf("</form>", formAt);
  const form = source.slice(formAt, formEnd);
  assert.doesNotMatch(form, /<dialog/);
  const elseAt = source.indexOf(") : (", source.indexOf("{blockForm ? ("));
  const dialogEnd = source.indexOf("</dialog>", formEnd);
  const nonBlock = source.slice(elseAt, dialogEnd);
  assert.match(nonBlock, /添加/);
  assert.match(nonBlock, /参与人（姓名）/);
  assert.match(nonBlock, /schedule-meet/);
  assert.match(source, /participantsForSubmit\(blockForm, guests\)/);
  assert.doesNotMatch(source, /splitGuests/);
  assert.doesNotMatch(source, /用顿号分隔/);
  assert.doesNotMatch(source, /将邀请：/);
  assert.doesNotMatch(source, /不邀请：/);
  const wire = readFileSync(new URL("../src/utils/guestWire.ts", import.meta.url), "utf8");
  assert.doesNotMatch(wire, /\.split\(/);
});

test("named guest round-trips as email and name", async () => {
  const { guestToWire, guestsFromWire } = await import("../src/utils/guestWire.ts");
  const wire = guestToWire({ kind: "named-invite", name: "张三", email: "zhangsan@example.com" });
  assert.deepEqual(wire, { email: "zhangsan@example.com", name: "张三" });
  assert.deepEqual(Object.keys(wire).sort(), ["email", "name"]);
  const legacy = guestsFromWire(["guest@example.com"]);
  assert.deepEqual(legacy, [{ kind: "email-only", email: "guest@example.com" }]);
  const named = guestsFromWire([{ email: "zhangsan@example.com", name: "张三" }]);
  assert.deepEqual(named, [{ kind: "named-invite", name: "张三", email: "zhangsan@example.com" }]);
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

test("formatFollowUtterance matches the pointed minutes sentence", async () => {
  const { claimFollowRows, formatFollowUtterance, mailtoHref } = await import(
    "../src/utils/followMail.ts"
  );
  assert.equal(
    formatFollowUtterance({ title: "客户会", start_at: "2026-10-22T15:00:00" }),
    "帮我整理这场会的纪要：客户会，2026-10-22 15:00 开始",
  );
  assert.equal(
    formatFollowUtterance({ title: "客户会", start_at: "2026-10-22", all_day: true }),
    "帮我整理这场会的纪要：客户会，2026-10-22 全天",
  );
  const inflight = { current: false };
  assert.deepEqual(claimFollowRows(["a1", "a2"], new Set(["a1"]), inflight), ["a2"]);
  assert.equal(inflight.current, true);
  assert.deepEqual(claimFollowRows(["a2"], new Set(), inflight), []);
  const href = mailtoHref({
    to: [{ email: "a@b.com", name: "张三" }],
    subject: "跟进：客户会",
    body: "张三周五前发报价",
  });
  assert.equal(href.startsWith("mailto:a@b.com?"), true);
});

test("follow mail buttons stay in the browser", () => {
  const source = readFileSync(new URL("../src/components/FollowupCard.tsx", import.meta.url), "utf8");
  assert.match(source, /复制邮件/);
  assert.match(source, /用邮件应用打开/);
  const copyAt = source.indexOf("function copyFollowMail");
  const nextFn = source.indexOf("\nfunction ", copyAt + 1);
  const copyFn = source.slice(copyAt, nextFn);
  assert.doesNotMatch(copyFn, /fetch\(/);
  assert.doesNotMatch(copyFn, /createCalendarEvent/);
  const openAt = source.indexOf("用邮件应用打开");
  const anchor = source.slice(source.lastIndexOf("<a", openAt), source.indexOf("</a>", openAt));
  assert.match(anchor, /mailtoHref/);
  assert.doesNotMatch(anchor, /fetch\(/);
  assert.doesNotMatch(anchor, /createCalendarEvent/);
});

test("chat renders followup outside the schedule card", () => {
  const source = readFileSync(new URL("../src/components/ChatWindow.tsx", import.meta.url), "utf8");
  assert.match(source, /FollowupCard/);
  const lowerAt = source.lastIndexOf("<ScheduleCard");
  const gate = source.slice(Math.max(0, lowerAt - 220), lowerAt);
  assert.match(gate, /!== "followup"/);
});

test("list rows offer follow-up beside prep", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  const listAt = source.indexOf('if (mode === "list")');
  const suggestAt = source.indexOf('if (mode === "suggest")');
  const branch = source.slice(listAt, suggestAt);
  assert.match(branch, /会前准备/);
  assert.match(branch, /会后跟进/);
  assert.match(branch, /formatFollowUtterance/);
});
