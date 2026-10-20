import assert from "node:assert/strict";
import test from "node:test";

import { foldThinking } from "../src/thinkingFold.ts";
import { overlayUnsavedError } from "../src/unsavedError.ts";

test("a finished reasoning-only row hydrates collapsed", () => {
  assert.equal(
    foldThinking("hidden", { type: "hydrate", reasoning: "thought", content: "" }),
    "collapsed",
  );
  assert.equal(
    foldThinking("hidden", { type: "hydrate", reasoning: "thought", content: "answer" }),
    "collapsed",
  );
});

test("reasoning opens the block and the first answer token collapses it", () => {
  const streaming = foldThinking("hidden", { type: "reasoning" });
  assert.equal(streaming, "streaming");
  assert.equal(foldThinking(streaming, { type: "answer" }), "collapsed");
  assert.equal(foldThinking("collapsed", { type: "toggle" }), "opened");
});

test("an unsaved warning does not replace the previous answer", () => {
  const previous = { id: 7, role: "assistant", content: "earlier", reasoning: "old" };
  const warning = { role: "assistant", content: "⚠️ 模型调用失败", reasoning: "new", fold: "streaming" };
  const merged = overlayUnsavedError([previous, warning], [previous]);
  assert.equal(merged.length, 2);
  assert.equal(merged[0].content, "earlier");
  assert.equal(merged[0].reasoning, "old");
  assert.equal(merged[1].content, "⚠️ 模型调用失败");
  assert.equal(merged[1].reasoning, "new");
});

test("a saved partial from this turn keeps the warning until the error row exists", () => {
  const warning = { role: "assistant", content: "⚠️ 模型调用失败", reasoning: "new" };
  const partial = { id: 9, role: "assistant", content: "half", reasoning: "new" };
  const merged = overlayUnsavedError([warning], [partial]);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].content, "⚠️ 模型调用失败");
  assert.equal(merged[0].reasoning, "new");
});
