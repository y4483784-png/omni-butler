import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { claimFollowRows } from "../src/utils/followMail.ts";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
const frontend = join(import.meta.dirname, "..");

const DRAFT = {
  id: "draft-1",
  text: "send notes",
  owner: "Ada",
  due_said: "",
  evidence: "",
  start_at: "2026-11-21T09:00:00",
  end_at: "2026-11-21T09:30:00",
  confirm_token: "token-draft",
  phase: "draft",
};

const SETTLED = {
  id: "settled-1",
  text: "book room",
  owner: "Ada",
  due_said: "",
  evidence: "",
  start_at: "2026-11-21T10:00:00",
  end_at: "2026-11-21T10:30:00",
  confirm_token: "token-settled",
  phase: "done",
};

function mixedCard() {
  return {
    title: "weekly",
    start_at: "2026-11-20T10:00:00",
    end_at: "2026-11-20T11:00:00",
    participants: [],
    phase: "draft",
    mode: "followup",
    status: "ready",
    expires_at: "2099-01-01T00:00:00Z",
    actions: [DRAFT, SETTLED],
  };
}

test("claimFollowRows posts only a draft row from a mixed-phase card", () => {
  const inflight = { current: false };
  assert.deepEqual(
    claimFollowRows(["draft-1", "settled-1"], new Set(), inflight, {
      "draft-1": "draft",
      "settled-1": "done",
    }),
    ["draft-1"],
  );
  const confirmed = { current: false };
  assert.deepEqual(
    claimFollowRows(["draft-1", "settled-1"], new Set(), confirmed, {
      "draft-1": "draft",
      "settled-1": "confirmed",
    }),
    ["draft-1"],
  );
});

test("a mixed-phase follow-up card posts only the draft action and disables the settled checkbox", async () => {
  const { Window } = await import("happy-dom");
  const window = new Window({ url: "http://localhost/" });
  const globals = {
    window,
    document: window.document,
    HTMLElement: window.HTMLElement,
    Element: window.Element,
    Node: window.Node,
    MutationObserver: window.MutationObserver,
    getComputedStyle: window.getComputedStyle.bind(window),
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
    cancelAnimationFrame: window.cancelAnimationFrame.bind(window),
  };
  const previous = new Map();
  for (const [key, value] of Object.entries(globals)) {
    previous.set(key, globalThis[key]);
    const desc = Object.getOwnPropertyDescriptor(globalThis, key);
    if (desc && desc.writable === false && desc.set == null) continue;
    globalThis[key] = value;
  }
  previous.set("fetch", globalThis.fetch);
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;

  const posts = [];
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    assert.equal(url, "/api/calendar");
    const body = JSON.parse(String(init?.body ?? "{}"));
    posts.push(body);
    if (body.confirm_token !== DRAFT.confirm_token) {
      return new Response(JSON.stringify({ detail: "closed" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(
      JSON.stringify({
        title: body.title,
        start_at: body.start_at,
        end_at: body.end_at,
        participants: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };

  const outfile = join(mkdtempSync(join(tmpdir(), "followup-mixed-")), "mount.mjs");
  let mounted;
  let root;
  try {
  await esbuild.build({
    stdin: {
      contents: `
        import { act, createElement } from "react";
        import { createRoot } from "react-dom/client";
        import { FollowupCard } from "./src/components/FollowupCard.tsx";
        export { act };
        export function mount(card) {
          const host = document.getElementById("root");
          const root = createRoot(host);
          act(() => {
            root.render(createElement(FollowupCard, { card, sessionId: 1 }));
          });
          return root;
        }
      `,
      resolveDir: frontend,
      sourcefile: "followupMixedPhase.entry.tsx",
      loader: "tsx",
    },
    bundle: true,
    format: "esm",
    platform: "node",
    outfile,
    jsx: "automatic",
  });

    const host = window.document.createElement("div");
    host.id = "root";
    window.document.body.appendChild(host);
    mounted = await import(pathToFileURL(outfile).href);
    root = mounted.mount(mixedCard());
    const boxes = [...window.document.querySelectorAll('input[type="checkbox"]')];
    assert.equal(boxes.length, 2);
    assert.equal(boxes[0].disabled, false);
    assert.equal(boxes[1].disabled, true);

    const button = [...window.document.querySelectorAll("button")].find((item) =>
      item.textContent.includes("确认加到日历"),
    );
    assert.ok(button);
    await mounted.act(async () => {
      boxes[1].click();
      boxes[0].click();
      button.click();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    assert.deepEqual(
      posts.map((body) => body.confirm_token),
      [DRAFT.confirm_token],
    );
    assert.equal(window.document.body.textContent.includes("有待办没有加进日历。"), false);
  } finally {
    if (root && mounted) {
      await mounted.act(() => {
        root.unmount();
      });
    }
    await window.happyDOM.abort();
    window.close();
    await esbuild.stop();
    // React's browser scheduler keeps a referenced port when window exists.
    for (const handle of process._getActiveHandles()) {
      if (handle.constructor?.name === "MessagePort") handle.unref();
    }
    for (const [key, value] of previous) {
      const desc = Object.getOwnPropertyDescriptor(globalThis, key);
      if (desc && desc.writable === false && desc.set == null) continue;
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});
