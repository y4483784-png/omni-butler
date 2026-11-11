---
name: verify-web-block
description: "Check that a web login never executes a local tool, that a desktop string on the body or the context does not become an allow, and that an unknown name still fails the whitelist. Use when editing the execution surface, invoke, or ChatRequest."
---

# Verify the web execution surface

A web login refuses a tool whose spec is marked local. `surface_for_login` takes no arguments and returns `ServerSurface.WEB`. `chat` calls it only after the user is authenticated. `ChatRequest` does not store a desktop field. `invoke` peeks the registry and returns the local refusal before `spec.execute`. A name with no spec, such as `shell`, still fails the whitelist. A confirm token is not an argument of `invoke`.

The question is an order of steps, so the check is TLC on a small model of those steps. The jar is the pinned file from `verification/tlc_jar.py`. Do not download another jar.

## When this applies

Run it when a change touches any of these:

- `backend/app/agents/harness/surface.py`
- `backend/app/agents/harness/gateway.py` (`invoke`)
- `backend/app/agents/harness/types.py` (`ToolContext.surface`)
- `backend/app/agents/tools/registry.py` (`ToolSpec.local`)
- `backend/app/api/chat.py` (`ChatRequest`, the `surface_for_login` call)
- `backend/app/agents/workflow.py` (the surface carried into `ToolContext`)

## Run

From the repo root, with Java on PATH:

```sh
python verification/web-block/check.py
python verification/web-block/check.py --break-spoof
python verification/web-block/check.py --break-local
python verification/web-block/check.py --break-shell
```

The held command exits 0. Each break command exits 1 and names its invariant. Exit 2 means the checker no longer matches the code, or TLC could not run.

Read a failing TLC trace with `/statechart`. Set `TLA2TOOLS` to the jar from `verification/tlc_jar.py` so that script does not download another TLC.

## What a failure means

`SurfaceStaysWeb` says that after the mint the surface is `web`. The harm is a body that says desktop becoming the server surface. In the trace, `Mint` sets `surface` to `desktop` while `claim` is `desktop` when `breakSpoof` is true. In the code, `surface_for_login` returns `ServerSurface.WEB` and does not read `ChatRequest`.

`NoLocalRun` says a local tool's execute count stays 0. The harm is `spec.execute` running a local program from the web page. In the trace, `Decide` sets `phase` to `executed` and `ran` to 1 while `kind` is `local` when `breakLocal` is true. In the code, `invoke` calls `local_tool_denial` and returns before `spec.execute` when the spec's `local` flag is true.

`ShellStaysWhitelist` says an unknown name ends on the whitelist denial. The harm is `shell` taking the local-tool sentence and skipping the whitelist. In the trace, `Decide` sets `phase` to `denied` while `kind` is `unknown` when `breakShell` is true. In the code, `get_tool("shell")` is missing, so `local_tool_denial` is not applied, and `_local_permission_ok` returns `工具不在白名单`.

`TokenStillDenied` says a local tool whose claim is `token` is still `denied` with `ran` 0. The harm is a confirm token unlocking a local tool. The held model has no action that reads `claim` inside `Decide`. In the code, `invoke` does not read `confirm_token`.

`TypeOK` says the variables stay in the sets above. TLC also checks deadlock. `Done` stutters after `Decide`.

If the trace cannot be replayed as `surface_for_login`, `coerce_surface`, and the peek in `invoke`, the model is wrong. Say so. Do not weaken the invariant to make the run pass.

## What this does not cover

The model is one turn. The claim is `none`, `desktop`, or `token`. The tool is `local`, `builtin`, or `unknown`. It does not model a second user, a stopped generation, the calendar confirm HTTP path, a desktop install package, or a real program. `TokenStillDenied` is the token case of the same no-execute rule. Live lanes are not part of this check.

## Keep the check with the code

The anchors were confirmed at `fd42ebbcee8e106aa501adb9ac639fc54dd051a4`. Move the confirmation forward only after reading `surface_for_login`, `coerce_surface`, `local_tool_denial`, the peek in `invoke`, the `ChatRequest` fields, and the `surface=` argument on `ToolContext`, and seeing that the model still describes them. If the mint can return a value other than `ServerSurface.WEB`, if `ChatRequest` grows a desktop field, or if `spec.execute` runs before the local denial, the checker exits 2. That is drift, not a passing run.

For a later edit that did not recheck the model, read `/maintain-formal-verification-skill`.
