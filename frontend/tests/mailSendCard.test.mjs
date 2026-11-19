import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const card = readFileSync(new URL("../src/components/MailSendCard.tsx", import.meta.url), "utf8");

test("the send card says sent only after the transport accepts", () => {
  assert.match(card, /card\.phase === "confirmed"/);
  assert.match(card, /confirmed \? "已发送"/);
  assert.match(card, /card\.phase === "unknown"/);
  assert.match(card, /"结果未知，未再次发送。"/);
  assert.match(card, /result\.state === "sent"/);
  const sentAt = card.indexOf('result.state === "sent"');
  const confirmAt = card.indexOf('phase: "confirmed"', sentAt);
  assert.ok(sentAt >= 0 && confirmAt > sentAt);
  assert.doesNotMatch(card, /已读/);
  assert.doesNotMatch(card, /gmail\.send/);
  assert.doesNotMatch(card, /mailtoHref/);
});

test("mailto stays on the followup card and is not a send", () => {
  const followup = readFileSync(new URL("../src/components/FollowupCard.tsx", import.meta.url), "utf8");
  const openAt = followup.indexOf("用邮件应用打开");
  const anchor = followup.slice(followup.lastIndexOf("<a", openAt), followup.indexOf("</a>", openAt));
  assert.match(anchor, /href=\{mailtoHref\(mail\)\}/);
  assert.doesNotMatch(anchor, /onClick/);
  assert.doesNotMatch(followup, /commitMailSend/);
});
