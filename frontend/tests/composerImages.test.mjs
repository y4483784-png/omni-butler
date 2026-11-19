import assert from "node:assert/strict";
import test from "node:test";

import {
  IMAGE_READ_ERROR,
  draftAfterReject,
  keepSentPreviews,
  removeDraftImage,
  serverDraftError,
  takeCompletedBatch,
  urlsReleasedWhenSent,
} from "../src/composerImages.ts";

const png = { id: "a", url: "blob:a", mediaType: "image/png" };
const gif = { id: "b", url: "blob:b", mediaType: "image/gif" };

test("a late read does not append and its url is released", () => {
  const stale = takeCompletedBatch([png], 1, 2, [gif], false);
  assert.deepEqual(stale.pending, [png]);
  assert.deepEqual(stale.releaseUrls, ["blob:b"]);
  assert.equal(stale.showError, false);
  const current = takeCompletedBatch([png], 2, 2, [gif], true);
  assert.deepEqual(
    current.pending.map((item) => item.id),
    ["a", "b"],
  );
  assert.deepEqual(current.releaseUrls, []);
  assert.equal(current.showError, true);
});

test("remove drops one image and returns only that url", () => {
  const removed = removeDraftImage([png, gif], "b");
  assert.deepEqual(
    removed.pending.map((item) => item.id),
    ["a"],
  );
  assert.equal(removed.releaseUrl, "blob:b");
  const missing = removeDraftImage([png], "gone");
  assert.equal(missing.releaseUrl, null);
  assert.equal(missing.pending.length, 1);
});

test("a rejected send restores the draft in the same session and releases it after a switch", () => {
  const same = draftAfterReject(3, 3, [png]);
  assert.deepEqual(same.restore, [png]);
  assert.deepEqual(same.releaseUrls, []);
  const switched = draftAfterReject(4, 3, [png]);
  assert.equal(switched.restore, null);
  assert.deepEqual(switched.releaseUrls, ["blob:a"]);
});

test("sent previews stay shown and a later sync keeps their urls", () => {
  assert.deepEqual(urlsReleasedWhenSent(["blob:a"], ["blob:a"]), []);
  assert.deepEqual(urlsReleasedWhenSent(["blob:a", "blob:b"], ["blob:a"]), ["blob:b"]);
  const before = [{ role: "user", content: "", localImages: [{ url: "blob:a" }] }];
  const synced = [{ id: 9, role: "user", content: "", localImages: [] }];
  const kept = keepSentPreviews(before, synced);
  assert.equal(kept[0].localImages[0].url, "blob:a");
});

test("the browser cap does not claim the server will accept 32MiB", () => {
  assert.match(IMAGE_READ_ERROR, /20MB/);
  assert.doesNotMatch(IMAGE_READ_ERROR, /不超过 32MiB/);
  assert.match(serverDraftError("size"), /20MB/);
  assert.match(serverDraftError("size"), /留在输入框/);
  assert.match(serverDraftError("body_limit"), /48MiB/);
  assert.match(serverDraftError("body_limit"), /留在输入框/);
  assert.match(serverDraftError("signature"), /留在输入框/);
});
