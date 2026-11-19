import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  beginSend,
  completeComposerRead,
  createComposer,
  mergeHydratedPreviews,
  previewUrls,
  rejectSend,
  releasePreviewUrls,
  removeComposerImage,
  setComposerText,
  switchComposerSession,
} from "../src/composerImages.ts";

function messagesAfterHydrate(before, synced) {
  return mergeHydratedPreviews(before, synced);
}

const source = {
  window: readFileSync(new URL("../src/components/ChatWindow.tsx", import.meta.url), "utf8"),
  stream: readFileSync(new URL("../src/services/chatStream.ts", import.meta.url), "utf8"),
  store: readFileSync(new URL("../src/store/useStore.ts", import.meta.url), "utf8"),
};

const png = { id: "a", url: "blob:a", mediaType: "image/png", dataBase64: "aaa" };
const gif = { id: "b", url: "blob:b", mediaType: "image/gif", dataBase64: "bbb" };

test("two empty sends keep each preview on its own message", () => {
  const firstSent = [{ role: "user", content: "", localImages: [{ url: "blob:a", mediaType: "image/png" }] }];
  const firstSync = [{ id: 1, role: "user", content: "" }];
  const first = messagesAfterHydrate(firstSent, firstSync);
  assert.equal(first.messages[0].localImages[0].url, "blob:a");
  assert.deepEqual(first.releaseUrls, []);
  const secondBefore = [
    ...first.messages,
    { role: "user", content: "", localImages: [{ url: "blob:b", mediaType: "image/gif" }] },
  ];
  const secondSync = [
    { id: 1, role: "user", content: "" },
    { id: 2, role: "user", content: "" },
  ];
  const second = messagesAfterHydrate(secondBefore, secondSync);
  assert.equal(second.messages[0].localImages[0].url, "blob:a");
  assert.equal(second.messages[1].localImages[0].url, "blob:b");
  const third = messagesAfterHydrate(second.messages, secondSync);
  assert.equal(third.messages[0].localImages[0].url, "blob:a");
  assert.equal(third.messages[1].localImages[0].url, "blob:b");
  assert.deepEqual(third.releaseUrls, []);
});

test("a removed message releases its preview and an older same text does not take the new one", () => {
  const before = [
    { id: 1, role: "user", content: "same", localImages: [{ url: "blob:old" }] },
    { role: "user", content: "same", localImages: [{ url: "blob:new" }] },
  ];
  const kept = messagesAfterHydrate(before, [
    { id: 1, role: "user", content: "same" },
    { id: 2, role: "user", content: "same" },
  ]);
  assert.equal(kept.messages[0].localImages[0].url, "blob:old");
  assert.equal(kept.messages[1].localImages[0].url, "blob:new");
  const removed = messagesAfterHydrate(kept.messages, [{ id: 2, role: "user", content: "same" }]);
  assert.equal(removed.messages[0].id, 2);
  assert.equal(removed.messages[0].localImages[0].url, "blob:new");
  assert.deepEqual(removed.releaseUrls, ["blob:old"]);
});

test("a stale read after a session switch does not append, and removal releases that url", () => {
  let composer = setComposerText(createComposer(), "hello");
  const ticket = composer.ticket;
  const switched = switchComposerSession(composer);
  const revoked = [];
  releasePreviewUrls(switched.releaseUrls, (url) => revoked.push(url));
  const read = completeComposerRead(switched.composer, ticket, [gif], false);
  releasePreviewUrls(read.releaseUrls, (url) => revoked.push(url));
  assert.equal(read.applied, false);
  assert.deepEqual(read.composer.pending, []);
  assert.deepEqual(revoked, ["blob:b"]);
  composer = completeComposerRead(switched.composer, switched.composer.ticket, [png, gif], false).composer;
  const removed = removeComposerImage(composer, "b");
  assert.deepEqual(removed.composer.pending.map((item) => item.id), ["a"]);
  assert.equal(removed.releaseUrl, "blob:b");
});

test("a rejected send keeps newer text and restores images that were not replaced", () => {
  const started = beginSend(setComposerText(createComposer(), "  old  "), false);
  assert.ok(started);
  assert.equal(started.composer.text, "");
  const typed = setComposerText(started.composer, "newer");
  const rejected = rejectSend(typed, started.claim, "size");
  assert.equal(rejected.composer.text, "newer");
  assert.equal(rejected.composer.pending.length, 0);
  assert.match(rejected.error, /20MB/);

  const withImage = beginSend(
    { ...setComposerText(createComposer(), "old"), pending: [png] },
    false,
  );
  assert.ok(withImage);
  const same = rejectSend(withImage.composer, withImage.claim, "body_limit");
  assert.equal(same.composer.text, "old");
  assert.equal(same.composer.pending[0].url, "blob:a");
  assert.deepEqual(same.releaseUrls, []);

  const moved = switchComposerSession(withImage.composer);
  const afterMove = rejectSend(moved.composer, withImage.claim, "size");
  assert.equal(afterMove.composer.text, "");
  assert.deepEqual(afterMove.releaseUrls, ["blob:a"]);
  assert.equal(afterMove.error, "");
});

test("removing a shown message releases its url, and the callers use this controller", () => {
  const messages = [{ id: 4, role: "user", content: "", localImages: [{ url: "blob:kept" }] }];
  const revoked = [];
  releasePreviewUrls(previewUrls(messages), (url) => revoked.push(url));
  assert.deepEqual(revoked, ["blob:kept"]);
  assert.match(source.window, /beginSend\(composerRef\.current, busy\)/);
  assert.match(source.window, /rejectSend\(composerRef\.current, started\.claim, result\.detail\)/);
  assert.match(source.window, /setComposerText\(composerRef\.current, e\.target\.value\)/);
  assert.match(source.window, /removeComposerImage\(composerRef\.current, id\)/);
  assert.match(source.window, /switchComposerSession\(composerRef\.current\)/);
  assert.match(source.stream, /messagesAfterHydrate\(before, synced, aborted\)/);
  assert.match(source.stream, /mergeHydratedPreviews\(before, merged\)/);
  assert.match(source.store, /previewUrls\(state\.sessionChats\[id\]\?\.messages/);
  assert.match(source.store, /releasePreviewUrls\(urls, revokeStoredPreview\)/);
  assert.doesNotMatch(source.window, /setInput\(text\)/);
});
