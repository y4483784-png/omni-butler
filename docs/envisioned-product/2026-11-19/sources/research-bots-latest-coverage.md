# Research-bot latest coverage audit (for cloud planner)

Date written: 2026-11-19, Asia/Singapore (UTC+8).
Purpose: attach to cloud plan `bc-96bf6f4b-09a5-50f5-a202-f1216e41f3e2`. This is an audit of existing Grok Bot teammate research, not a new market crawl or source review.
Do not treat anything below as source-verified unless the Evidence level says so.

## 1. Scope and method

Read the recent records of three research bots only (no peer messages, no new routines, no repo code review, no cloud-agent changes):

- X侦察 `deb28aec-3d4d-4f07-a65b-61710b4d662d` — weekday X/public-page signal briefs, 2026-11-04 through 2026-11-19 14:22 SGT (Linger).
- 新派Agent `7af2cc4c-eaa7-4dbe-80e8-3ccdab6bda1e` — new-wave personal-agent briefs plus earlier DSH/product-map work, 2026-11-04 through 2026-11-19 09:32 SGT.
- 壳子分析 `282a8387-cdd1-468a-9d0a-174aeb780af8` — source dives of Pi (2026-11-04) and OpenMausBot/DS-Bot plus harness map (2026-11-06). No later source dive appears in the record after 2026-11-06.

Compared against current planning inputs (mtime 2026-11-19 19:01 SGT):

- `/workspace/omni-plan-inputs-20261119/current-plan-page.txt`
- `/workspace/omni-plan-inputs-20261119/oss-issue-references.md`

and the dumped cloud transcript `/workspace/cloud-agent-transcripts/bc-96bf6f4b-09a5-50f5-a202-f1216e41f3e2.jsonl` (mtime 2026-11-19 20:45 SGT).

The confirmed 16 main issues are M1–M6, T1–T5, X1–X5. R-B3 and R1–R7 are residuals and are not treated as the main set.

Evidence levels used below:

- **signal-only**: official post, blog, product page, or press. Not a substitute for code.
- **README / docs / release notes**: public written claims. Not call-chain proof.
- **source-verified (prior)**: a bot or the later OSS pass actually read pinned source. This audit did not re-open those repos.

Absence of a project name is not proof that the mechanism is uncovered, if an overlapping mechanism is already cited (for example SuperAgent dispatch, Hermes activity timing, OpenMausBot device revoke, Browser Use as a browser candidate).

String checks: `Linger`, `heylinger`, `simplexai`, `n8n-io/nod`, `lumo-desktop`, `sentient-os`, `opencrawl`, `Poppy`, `anomalyco/browse`, `pi-durable`, and `createAgentSession` are absent from both current inputs and the dumped cloud transcript. The bot names Ｘ侦察 / 新派Agent / 壳子分析 are also absent from that transcript. The cloud run did read the two input files.

## 2. What already entered the current plan

Earlier teammate work *was* used, mainly through mechanisms later re-anchored in the 2026-11-19 OSS pass. It is not accurate to say the research bots were unused.

Already in the 16-issue page / OSS references:

| Mechanism already adopted | Main issues | Source now cited in inputs | How it first showed up in teammate research |
| --- | --- | --- | --- |
| SuperAgent / dsh-superagent-prototype: dispatch ≠ execute, Ticket / Mandate, subscription pass-through, observe-default | T1, T2 (product skeleton) | Plan T1/T2 explicitly keep this split; prototype is named | 新派Agent 2026-11-04 README+design-doc brief: research prototype, MIT, Router must not do the work; do not fork worktree/PR/danger-full-access |
| Hermes tool loop, role-specific models, `last_activity_at` stall timing, session attach/detach | M1–M3, T3, T4, X2 | Hermes `0e8e4216…` (2026-11-18) in the OSS pass | X侦察 2026-11-06 reported v0.23 Hearth (signal). The plan’s file:line coverage comes from the later OSS pass, not from that short brief |
| OpenCode queue / explicit redirect / stop separation | T1, T2 (local parts only; default steer is not copied) | OpenCode `79bd2c6f…` | Not a new 11-09+ finding; already in the OSS pass |
| OpenMausBot device hash + per-device revoke; older cancel/receipt/approval-fingerprint work | X5; supporting notes for cancel and receipts | Device registry re-checked at HEAD `35d04429…`; old pin `083fd7b…` still cited | 壳子分析 2026-11-06 source-verified at `083fd7b…` |
| DS-Bot mail send after provider return, cancel chain, memory ledger, visibility ≠ execute | X1 (partial), T2/T3 supporting | Mail/redirect re-checked at HEAD `5e9b7da8…`; old pin `71d344b…` | 壳子分析 2026-11-06 source-verified at `71d344b…` |
| OpenClaw ExecApprovalLifecycle (one decision, consume once, continue same path) | X4 | OpenClaw HEAD `069c3e2b…` | Related Pi/OpenClaw design advice from 壳子分析 2026-11-04 (do not treat Pi as a forever runtime; confirmation gate lives in the worker, not the dispatcher) |
| Browser Use as a *candidate* browser backend, via Hermes integration clues | X3 research direction only | Plan/OSS: Hermes + Browser Use is an integration clue, not a selected dependency | Overlaps X侦察 2026-11-10 Browser Use 1.0 signal; the plan already had this candidate and does not cite the 1.0 migration caveats below |
| DeepSeek Harness independent Search/Fetch | M1 | Harness `ca33fd68…` | Adjacent to DSH/Harness tracking; not a 11-09+ omission |

Caveats already recorded by 壳子分析 (2026-11-06) and still valid:

- Event-delivery dedup is not exactly-once external side effects.
- Stop is not revoke of a request already sent.
- OpenMausBot watchdog is an in-process timer, not an independent supervisor session.
- Approval maps in memory are not a durable cross-restart confirmation store.
- Do not copy chat-gateway / group-permission product scope.

Those caveats are about limits of the *already adopted* references. They are not new coverage holes created by the later briefs.

## 3. Useful omissions and missing follow-ups

These are the findings most worth handing to the planner. None of them were source-verified in the teammate records, except where noted for Pi docs.

### Linger — highest-priority missing *name*, mechanism overlaps X1/X2/T3

- **What was reported:** Local personal agent (not a coding shell) on macOS / Windows / Linux. Watches mail and calendar and alerts proactively. Can run local models or attach Claude Code / Codex / Pi. Official claim that a 12GB GPU can run a 125B model is unverified.
- **Bot / date:** X侦察, 2026-11-19, reported to user ~14:22 SGT; founder post 10:09 SGT. 新派Agent’s 11-19 brief did *not* include it (that brief was Perplexity Teammates).
- **Links:** https://heylinger.com/ · https://docs.heylinger.com/ · https://github.com/simplexai-labs/linger · https://x.com/zhangboknight/status/2123237197050794364
- **License:** Modified Apache-2.0 with extra commercial conditions. Do not treat as stock Apache-2.0. Do not copy code until legal review.
- **Evidence level:** signal-only (official post + product/docs/repo links). Bot explicitly said source was not verified.
- **Relevance:** X1 (mail success/receipt), X2 (calendar write + proactive follow-up), T3 (local always-on / persist). Weaker for M1–M6.
- **Current coverage:** Name absent from both inputs and the dumped cloud transcript. That does *not* mean mail/calendar receipts are uncovered: X1 already cites DS-Bot `mail_send` after provider return; X2 already cites Hermes `calendar_create` after remote id. Linger is a newer *local personal-agent* candidate in the same problem space, not a proven replacement.
- **Verify before adopting:** LICENSE/NOTICE commercial terms; whether mail/calendar alerts are actually wired and whether they record provider ids / idempotency keys; persist-across-restart behavior; no copy of modified-Apache code into this repo without a license decision. GPU/model claims can be ignored for the 16 issues.

Timing note: Linger was already public hours before the 19:01 SGT input files were written, and still did not enter them.

### Nod (n8n Labs) — highest-priority missing *open-source personal* follow-up for T1/X1/X2/X4

- **What was reported:** Research preview personal agent, independent of n8n. Local sandbox computer + browser on Mac, Windows 11 preview. Gmail/Calendar connectors. One chat front-end, parallel background workers, scheduled jobs. Trust checks before send, pay, or delete. Sleeping the machine stops work. v0.7.6 (2026-11-14 06:45 SGT) adds Automatic preparation: on Gmail/calendar changes, pre-prepare a nudge or task; default off; supports quiet hours.
- **Bot / date:** X侦察 2026-11-11 (launch); 新派Agent 2026-11-11 and 2026-11-16 (0.7.6).
- **Links:** https://github.com/n8n-io/nod · https://blog.n8n.io/meet-nod/ · https://nod.n8n.io · https://github.com/n8n-io/nod/releases/tag/nod-v0.7.6
- **License:** Apache-2.0 (as reported). Still confirm LICENSE at the SHA you read.
- **Evidence level:** official blog + README/release notes. Not source-verified.
- **Relevance:** T1 (chat continues while workers run), X1/X2 (mail/calendar change observation), X4 (trust gate before irreversible actions), T4 (quiet hours / no fake heartbeat). Also adjacent to the Browser Use candidate, but Nod is a product shell with a browser, not a drop-in Browser Use replacement.
- **Current coverage:** `n8n-io/nod` is absent. Overlap already in the plan: SuperAgent front-door + worker, OpenMausBot/OpenClaw approval gates, Hermes activity/watchdog. The specific “observe mail/calendar delta, default-off auto-prepare, quiet hours” path is not cited.
- **Verify before adopting:** Trust-check call chain and whether a consumed approval is bound to action+params; how Gmail/calendar change detection is persisted and deduped (compare X1/X2 receipts); sleep/stop behavior vs T3 “disconnect must not cancel server work” (Nod is local and stops on sleep — likely a contrast, not a copy); preview maturity.

### Proton Lumo Desktop — useful X1/X4 schedule-policy reference, license is the gate

- **What was reported:** Privacy chat app adds desktop doing-work: local files, mail, drive, MCP, schedules, Skills. Each schedule can be set independently to auto / ask first / forbid. Reading summaries is looser; send and delete stay behind approval. Not a Router/Worker design. Small project (~37★ when reported).
- **Bot / date:** 新派Agent 2026-11-18 (repo published 2026-11-17 15:20 SGT).
- **Links:** https://proton.me/blog/lumo-desktop-features · https://github.com/ProtonLumo/lumo-desktop
- **License:** GPL-3.0. Usable as a behavior reference; copying or combining code needs a GPL obligation review. The current plan already says GPL-3.0 is not “unusable,” but is not a casual copy license.
- **Evidence level:** official blog + repo announcement. Not source-verified.
- **Relevance:** X1 (send gated, read looser), X4 (per-action policy), T2 (do not auto-expand authority).
- **Current coverage:** Name absent. Approval-once and mail-after-provider-return are already covered by OpenClaw/FullDots and DS-Bot. The missing piece is *per-schedule* auto/ask/forbid, which the 16-issue page does not cite.
- **Verify before adopting:** Confirm the per-schedule policy exists in code; do not copy GPL sources without legal review.

### OpenBot (CopilotKit/OpenBot) — T1/T2/T4 task-lifecycle ideas; name collision

- **What was reported:** Self-hosted workspace, MIT, personal desktop plus a team edition. Subagents, per-bot isolation, tool-level approve/deny/audit, scheduled check-ins. 1.1.0 (2026-11-17): main bot delegates; cards and a “Needs you” state stay in the thread; when the goal is reached the bot stops and turns off routines started for that goal. 新派Agent called it more coworker than life-admin.
- **Bot / date:** X侦察 2026-11-11; 新派Agent 2026-11-11 and 2026-11-18.
- **Links:** https://github.com/CopilotKit/OpenBot · https://open.bot · https://www.copilotkit.ai/blog/introducing-openbot-1-0 · https://github.com/CopilotKit/OpenBot/releases/tag/v1.1.0
- **License:** MIT (as reported). Confirm at SHA. X侦察 warned the name collides with other “OpenBot” projects; use this repo, not a namesake.
- **Evidence level:** official blog + release notes. Not source-verified.
- **Relevance:** T1/T2 (task cards, human-needed state, delegation), T4 (stop when the goal is done, not only when a timer fires).
- **Current coverage:** Name absent. Dispatch/worker split is already SuperAgent; approval consumption is already OpenClaw/FullDots. “Goal reached ⇒ stop and disable the routines started for that goal” is not cited.
- **Verify before adopting:** Whether routine shutdown is actually tied to goal completion in code; that it cannot kill an unrelated task (T2 cancel-scope rule).

### OpenCode Browse — addendum to the existing X3 browser candidate, not a blank hole

- **What was reported:** MCP + Chromium extension so coding agents use the operator’s already-logged-in browser. Login, captcha, and payment are handed back to the human. High permission risk because the account session is shared.
- **Bot / date:** X侦察 2026-11-09.
- **Links:** https://opencode.ai/extensions/browse · https://github.com/anomalyco/browse
- **License:** reported as open source; confirm LICENSE at SHA.
- **Evidence level:** product page / repo announcement. Not source-verified.
- **Relevance:** X3 (browser as a fallback when no API exists) and X4 (human step before login/payment).
- **Current coverage:** The plan already has a Browser Use candidate and says it is not selected. `anomalyco/browse` and “OpenCode Browse” are absent. This is a missing *second* logged-in-browser reference, not “no browser coverage.”
- **Also fold in Browser Use 1.0 caveats** (X侦察 2026-11-10, https://github.com/browser-use/browser-use , MIT): official 1.0 rewrite writes Chrome DevTools Protocol from a persistent Python REPL; official speed/cost claims are not independently benchmarked; default `Browser()` now goes to the vendor cloud, local use needs `Browser('chromium')`. Those migration details are not in the current inputs.
- **Verify before adopting:** Session-sharing / credential boundary; whether payment/login hand-back can sit in front of Omni-Butler confirmation cards; for Browser Use 1.0, the cloud-default footgun and Flash compatibility already listed as open X3 work.

### pi-durable — addendum to T3, docs-level only

- **What was reported:** Experimental durable harness: conversations, turns, and tool calls are committed before display; process death can resume; a request that dies mid-flight is a failed attempt and is retried. Distinct from core Pi, which has no first-class long-term memory, channels, or heartbeat. 壳子分析 conclusion: Pi is a coding harness, not a personal-agent OS; do not wholesale-fork; confirmation cards belong in the product UI, in the worker/tool layer; OpenClaw has already internalized `@openclaw/agent-core` and is no longer on `pi-coding-agent`.
- **Bot / date:** 壳子分析 2026-11-04, after reading Pi docs/README and (for durable) package README.
- **Links:** https://github.com/earendil-works/pi · https://pi.dev/docs/latest/sessions/ · package `@earendil-works/pi-durable` README (persist and resume) · OpenClaw internalization: https://github.com/openclaw/openclaw/blob/main/docs/agent-runtime-architecture.md
- **License:** MIT (Pi, as reported). Confirm at SHA. `pi-durable` is experimental and API-unstable per its README.
- **Evidence level:** README/docs (bot read them). Not a fresh 2026-11-19 source re-verification.
- **Relevance:** T3 (server-persistent tasks, reconnect must not cancel work, restart recovery).
- **Current coverage:** T3 already cites Hermes attach/detach and says this product will not copy “close when the last subscriber leaves.” `pi-durable` / `createAgentSession` are absent. This is a missing *secondary* persist/resume reference, not an uncovered T3.
- **Verify before adopting:** Whether mid-turn commit/resume is still accurate in current `pi-durable`; retry-on-death must not replay non-idempotent writes (X1/X2/X3 “unknown ⇒ query, do not redo”); do not take a hard dependency on an experimental package.

### Sentient OS 3.1 — X1/X2 proactive digest pattern, AGPL and platform limits

- **What was reported:** Overnight on-device digestion of authorized calendar, mail, and notes into editable local Markdown; Sidekick can then draft actions (times, replies) for approval. Notch UI is a new entry point, not new orchestration. Apple-silicon Mac only. About 730★ / vendor claim of 10k+ users, unverified here.
- **Bot / date:** 新派Agent 2026-11-13.
- **Links:** https://github.com/Sentient-OS-Labs/sentient-os · https://sentient-os.ai/
- **License:** AGPL-3.0. Do not merge into a closed distribution without an AGPL review. Same class of caution the plan already applies to Khoj.
- **Evidence level:** signal + repo/product page. Not source-verified.
- **Relevance:** X1/X2 (nightly mail/calendar digest, then approval drafts). Overlaps SuperAgent observe-default and “speak only when a result or a human action is needed.”
- **Current coverage:** Name absent. Pattern partly covered by observe-default + confirmation cards. The specific overnight-digest + local Markdown knowledge folder is not cited.
- **Verify before adopting:** License; that drafts cannot write calendar/mail until approval; platform constraint (Apple silicon only) probably makes it a behavior reference, not a dependency.

## 4. Related signals — use only as product context, not issue references

These were reported by the bots, are absent by name from the current inputs, and should not be promoted to pinned OSS references without a later source or legal pass. Several also collide with already decided product limits (no phone app, no cloud phone, no external chat gateway).

| Candidate | Bot / date | License / openness | Possible overlap | Why not a current reference |
| --- | --- | --- | --- | --- |
| K-AI / Kai (https://getk-ai.com/blog/introducing-k-ai/) | 新派 2026-11-09 | Closed product | X4 / isolation: Linux VM, approve spend/send/delete | Signal-only, indie preview, no source |
| Nico (https://nico.app/) | 新派 2026-11-06 | Closed product (as reported) | T1: explicit front agent + worker, risk tiers | Signal-only, volume unproven |
| ChatGPT Wallet | X侦察 2026-11-16 | Closed | X3/X4: per-payment biometric approval, agent sees a token not the card | Closed, US Pro dots only; already “no unpaid auto-pay” |
| Personal Agent Protocol / Poppy 1.0 (https://sierra.ai/blog/releasing-poppy) | X侦察 2026-11-16 | Open standard (claim) | X3 merchant interaction | Protocol, not a receipt/idempotency implementation |
| Alice AI “Do it!” (https://yandex.com/company/news/2026-11-13) | 新派 2026-11-16 | Closed | T1 + X4: main orchestrator delegates browse/phone workers; approve call/order/booking | Closed cloud VMs |
| Perplexity Teammates / Multiagent | 新派 2026-11-06, 2026-11-19 | Closed | T1: workers compare notes mid-flight | Closed, work-oriented, mobile entry is out of scope |
| Muse Spaces / Mail / Outbound calling | X侦察 + 新派, 11-06 to 11-13 | Closed | “Keep updating while you are away”; mail as a first-class channel | Closed consumer product; mail-as-entry is already a product decision, not a missing mechanism citation |
| Inkbox hosted agents, 千问 Go, Phone Harness, DSH Anywhere, Herdr mobile, OpenCode Android, Grok Bot Mac apps | X侦察 11-05 to 11-13 | Mixed; several closed | Mobile / cloud-computer reachability | Conflicts with “no phone product / no cloud phone” |
| Agent Autoloop (https://github.com/KKKKhazix/agent-autoloop), MIT | X侦察 2026-11-06 | MIT, README-level | T1/T4: cron/Issue/webhook intake, ask only when unsure | Coding scheduler; no source read |
| huggingface/safeagents, Stroq, BAS Guard, vercel-labs/agent-desktop, Dim/DimAgent | X侦察 11-04 to 11-06 | Mixed open | Weak for the 16 issues (coding safety or screenshot-free computer use, which is deferred) | 壳子分析 never followed Dim; computer-use remains postponed |
| OpenCrawl, Stitch Loop, Devin Bots/Apps, Second (https://2.computer), Antigravity family, Exo | X侦察 11-06 to 11-19 | Mixed; several closed | Multi-agent context or coding product infra | Outside the 16 issues; “Second” in the cloud dump is the English word, not this product |
| 千问办公 2.0, Kimi Hosted Agents, Vorflux, Cursor Projects, Claude Managed Agents | X侦察 | Closed / enterprise | Team/hosted harness market context | Not personal-butler mechanisms |

## 5. What to do in the plan (and what not to do)

1. Keep the already pinned references for T1/T2 (SuperAgent), T3/T4/M2 (Hermes), X1 (DS-Bot mail), X2 (Hermes calendar id), X4 (OpenClaw/FullDots), X5 (OpenMausBot), and the Browser Use *candidate* for X3. Do not replace them with un-read new names.
2. Add a short “recent research-bot follow-up” note, not a new architecture decision: Linger, Nod, Lumo, OpenBot 1.1, OpenCode Browse, pi-durable, Sentient OS, plus Browser Use 1.0’s cloud-default caveat.
3. If any of those is promoted to a pinned reference, source-read a fixed SHA first and record license terms. Modified Apache (Linger), GPL-3.0 (Lumo), and AGPL-3.0 (Sentient, and previously Khoj) are not interchangeable with MIT/Apache-2.0.
4. Do not claim “no coverage” for mail receipts, calendar ids, dispatch/worker, watchdog timing, or browser fallback merely because these newer names are missing.
5. This file is planning input. It does not change the existing Page, does not approve implementation, and does not verify current Omni-Butler code.
