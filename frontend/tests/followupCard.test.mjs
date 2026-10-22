import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const OFFICIAL_PASTE = "这场会还没有官方纪要。把会议记录或转写贴过来。不会编造不存在的纪要。";
const ATTACHMENT_CONSENT = "日历上有纪要附件。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。";
const MEET_CONSENT = "这场会有 Meet 智能纪要。要打开它，需要授权读取云端硬盘。不会把它当成没有纪要。";
const UNAVAILABLE = "暂时读不到这场会的官方纪要。可以把记录贴过来。不会编造官方纪要。";

test("followup card names the official source and does not reconnect for Meet", () => {
  const source = readFileSync(new URL("../src/components/FollowupCard.tsx", import.meta.url), "utf8");
  assert.match(source, /来自 Meet/);
  assert.match(source, /来自日程附件/);
  assert.match(source, new RegExp(OFFICIAL_PASTE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(source, new RegExp(ATTACHMENT_CONSENT.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(source, new RegExp(MEET_CONSENT.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(source, new RegExp(UNAVAILABLE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(source, /授权读取 Drive/);
  assert.match(source, /connectGoogle/);
  assert.match(source, /claimFollowRows/);
  assert.match(source, /postFollowAction/);
  assert.match(source, /formatFollowUtterance/);
  assert.doesNotMatch(source, /重新连接/);
  assert.doesNotMatch(source, /gmail\.send/);
  const confirmAt = source.indexOf("async function confirm");
  const postAt = source.indexOf("postFollowAction", confirmAt);
  assert.ok(confirmAt >= 0 && postAt > confirmAt);
  assert.doesNotMatch(source.slice(confirmAt, postAt), /onAsk/);
});

test("chat passes onAsk into the followup card", () => {
  const source = readFileSync(new URL("../src/components/ChatWindow.tsx", import.meta.url), "utf8");
  assert.match(source, /<FollowupCard card=\{m\.scheduleCard\} onAsk=\{onAsk\} \/>/);
});
