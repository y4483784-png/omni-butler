# Open-source personal / second-brain agents — comparison (2026-11-04)

Research snapshot for deciding whether to **fork** a personal-agent stack vs **evolve** an existing Python+TS calendar butler (Omni-Butler-like). No commits made. Times below are **Asia/Singapore (UTC+8)** unless marked otherwise.

**Method:** GitHub `get_repository` + `get_file_contents` on READMEs/architecture entrypoints; docs via WebFetch; light adjacent notes for Eigent / Open Interpreter.

---

## Cross-cutting verdict (for a calendar butler)

| Goal | Better move |
|---|---|
| Keep calendar/mail confirm-card product | **Evolve your repo** — do not replace with a full fork |
| Steal memory / channel / tool patterns | Read **Leon** (layered memory), **Letta Code** (MemFS + channels), **AnythingLLM** (Gmail/Calendar plugins already exist) |
| Steal RAG / docs second-brain | **Khoj** or **AnythingLLM** (AnythingLLM more actively pushed) |
| Desktop multi-agent “cowork” | **Eigent** (adjacent; different product) |
| Local coding / computer use only | **Open Interpreter** (contrast — not a personal assistant product) |

**Hard fork recommendation:** None of the four deep projects is a drop-in base for a production calendar butler. Closest *feature* adjacency for mail/calendar tools is **AnythingLLM**’s AIbitat plugins (`gmail`, `google-calendar`, `outlook`). Closest *personal-agent identity* architecture is **Leon 2.0 develop**. Closest *long-horizon memory* research product is **Letta Code**. Closest *classic second-brain RAG* is **Khoj** (but last push ~3 months quieter).

---

## 1. Khoj — deep

### Repo meta
| Field | Value |
|---|---|
| Repo | [khoj-ai/khoj](https://github.com/khoj-ai/khoj) |
| License | **AGPL-3.0** |
| Language | Python (primary); web UI + clients |
| Stars | **37,973** |
| Last push | **2026-08-02 09:55 Asia/Singapore** (`pushed_at` 2026-08-02T01:55:40Z) |
| Homepage | https://khoj.dev · docs https://docs.khoj.dev |

### Positioning
“Your AI second brain.” Self-hostable chat over local/online LLMs + personal docs; custom agents; scheduled automations / newsletters; clients for Browser, Obsidian, Emacs, Desktop, Phone, WhatsApp. README also points to sibling **Pipali** (“AI coworker that runs on your computer”).

### Architecture (evidence)
**Services** (`docker-compose.yml`):
- `server` — Khoj app (`ghcr.io/khoj-ai/khoj`), port **42110**
- `database` — `pgvector/pgvector:pg15`
- `sandbox` — Terrarium Python sandbox (`KHOJ_TERRARIUM_URL`)
- `search` — SearxNG (`KHOJ_SEARXNG_URL`)
- `computer` (optional) — `khoj-computer` + VNC `:5900`; gated by `KHOJ_OPERATOR_ENABLED`

**Code layout** (`src/khoj/`):
- Django-style app (`manage.py`, `database/`, `routers/`, `configure.py`, `main.py`)
- `processor/content` — ingestion
- `processor/embeddings.py` — embeddings
- `processor/conversation` — chat
- `processor/tools/` — `online_search.py`, `run_code.py`, `mcp.py`
- `processor/operator/` — computer-use path
- `processor/speech`, `processor/image`
- `interface/web`, `interface/email`
- Semantic search via `search_type/`, `search_filter/`

**RAG / memory:** Document sync (Desktop/Obsidian/Emacs/Notion/drag-drop) + pgvector; chat with docs + web. Not MemGPT-style editable agent memory blocks; more “second brain over files.”

**Tools:** Web search (SearxNG / Serper / Firecrawl / Exa…), code sandbox (Terrarium or E2B), MCP, optional computer operator.

**UI / channels:** Web app; Desktop / Obsidian / Emacs / WhatsApp clients (docs); email interface package in-tree.

### Capability boundary
**CAN:** Private RAG Q&A; multi-model chat; custom agents; research automation / notifications; Obsidian/Emacs sync; optional sandboxed code + computer; WhatsApp access path.

**NOT (or weak vs a butler):** First-class Google Calendar/Gmail OAuth product loop with confirmation cards is not the core pitch; AGPL viral license; optional computer is container/VNC-shaped, not a polished multi-channel always-on personal OS agent; upstream push cadence quieter since Aug 2026.

### Pros / cons for forking vs evolving a calendar butler
**Pros:** Python stack affinity; strong RAG/docs story; Docker+pgvector known shape; MCP + search + sandbox already wired.

**Cons:** **AGPL-3.0** is a legal/product constraint for proprietary SaaS forks; large Django monolith; product center of gravity is knowledge, not calendar workflow; quieter recent commits vs peers.

### Forkability
Technically forkable; license is the main gate. Better as **reference / optional RAG sidecar** than as the new monorepo root.

### Paths read
`README.md`, `docker-compose.yml`, `src/khoj/` (listing), `src/khoj/processor/` + `tools/`, `src/khoj/interface/`, docs `get-started/setup`.

---

## 2. Letta (MemGPT lineage) — deep

### Repo resolution (important)
| Repo | Role | Stars | Last push (Asia/Singapore) |
|---|---|---|---|
| [letta-ai/letta](https://github.com/letta-ai/letta) | **Landing / archive pointer** (legacy Python V1 on `archive` branch; unsupported) | **25,345** | 2026-10-19 06:28 |
| [letta-ai/letta-code](https://github.com/letta-ai/letta-code) | **Current implementation** (harness, TUI, App Server, channels) | **3,778** | **2026-11-04 15:46** |

Evidence: `letta` README + `AGENTS.md` say do **not** use `main` as working code; develop in `letta-code`. Package: `@letta-ai/letta-code` / `letta` CLI. License on both: **Apache-2.0**. Language: **TypeScript** (letta-code).

### Positioning
Stateful agents “more like people than tools”: memory, identity, experience over time; self-edit memory/skills/prompts; interactive CLI + always-on; channels; optional Letta Cloud for agent state across machines.

### Architecture (evidence)
From `letta-code` README + `src/` + `src/channels/README.md` + docs memory page:

- **CLI / TUI** (`letta`) and **App Server** (`letta server`)
- **MemFS** — git-backed memory filesystem; agents rewrite memory; `/memory-repository` can sync to GitHub
- **Dreaming / sleeptime** — background reflection subagents consolidate lessons (`/sleeptime`, `/dream`)
- **Skills** — `~/.letta`, `.agents/skills`, agent-scoped skills in MemFS
- **Subagents** — general-purpose / forked / recall; agent-to-agent
- **Channels** — first-party Telegram, Slack, Discord; user plugins under `~/.letta/channels/<id>/` with pairing/routing/access control
- **Hooks, permissions, crons/schedules, reminders**
- **Remote computers** — `letta server --computer-name …` (Cloud sign-in for multi-machine)
- **MCP** client runtime in `src/mcp-*.ts`
- **AgentFile (.af)** — **deprecated/removed** from Letta Code (migration hazard)

Cloud default vs local backend switch (`letta backend cloud|local`).

### Capability boundary
**CAN:** Long-lived stateful coding/personal agents; git-versioned memory; channel presence; schedules/heartbeats; multi-machine routing; skill learning; permission modes.

**NOT:** Drop-in second-brain doc RAG product like Khoj/AnythingLLM; not a calendar-confirm-card SaaS; Free/Pro cloud memory size caps (docs: 5 MiB / 50 MiB per memory repo); Windows auto-dreaming disabled by default; community stars still concentrated on archived `letta` name.

### Pros / cons vs calendar butler
**Pros:** Best-in-class **open memory research product**; Apache-2.0; channel plugin model is copy-worthy; schedules align with “proactive” personal agent goals.

**Cons:** TS-first rewrite — poor drop-in for a Python backend; product is agent harness / coding coworker, not mail/calendar domain; Cloud gravity; forking means adopting a fast-moving TS monorepo (~3.9k stars on active code, huge `src/`).

### Forkability
Apache-friendly. Fork if you want a **stateful agent OS**. Do **not** fork legacy `letta` Python archive. Integrating Letta as a sidecar via SDK/App Server is more realistic than replacing a butler codebase.

### Paths read
`letta` README + AGENTS.md (via search/docs); `letta-code` README; `src/` listing; `src/channels/README.md`; https://docs.letta.com/letta-code/memory.

---

## 3. AnythingLLM — deep

### Repo meta
| Field | Value |
|---|---|
| Repo | [Mintplex-Labs/anything-llm](https://github.com/Mintplex-Labs/anything-llm) (**confirmed current**) |
| License | **MIT** |
| Language | JavaScript (Node server + React frontend) |
| Stars | **67,911** |
| Last push | **2026-11-04 10:18 Asia/Singapore** (`pushed_at` 2026-11-04T02:18:15Z) |
| Latest noted release | v1.19.0 (2026-10-26) |

### Positioning
Local-first all-in-one ChatGPT-like app: chat with docs, agents, multi-user (Docker), MCP, no-code agent flows, desktop + Docker + mobile sibling repos. Tagline shifted toward “agent experience” / computer-use topics.

### Architecture (evidence)
Monorepo (README Technical Overview):
- `frontend` — Vite + React
- `server` — Express; Prisma DB; vector DB management; LLM providers
- `collector` — document parsing service
- `docker`, `embed`, `browser-extension` submodules
- Default vector DB: **LanceDB**; also PGVector, Pinecone, Chroma, Weaviate, Qdrant, Milvus…

**Agents** (`server/utils/agents/`):
- `AIbitat` multi-agent framework
- `ephemeral.js` — one-off agent runs (HTTP/API), loads history, MCP, AgentFlows, imported plugins
- Built-in plugins include **`gmail`**, **`google-calendar`**, **`outlook`**, `web-browsing`, `web-scraping`, `filesystem`, `sql-agent`, `memory`, `create-scheduled-job`, `request-user-input`, etc.

**Memories** (docs): workspace (≤20) + global (≤5) facts; Observer/Reflector auto-extraction on idle cron; injected into system prompt — **shallow personalization**, not Leon/Letta-scale memory.

**Also:** scheduled jobs, model router, MCP, embeddable widget (Docker), built-in local LLM engine (Lemonade/llama.cpp).

### Capability boundary
**CAN:** Production-ish self-hosted RAG workspace; multi-user Docker; agent tools including **mail/calendar**; MCP; cron agents; desktop app; many LLM backends.

**NOT:** Deep MemFS / dreaming; not primarily a voice OS assistant; memory caps are small; telemetry on by default (opt-out); agent UX is workspace-chat-centric (`@agent`), not a dedicated calendar product with formal confirm cards / TLC-style specs.

### Pros / cons vs calendar butler
**Pros:** **MIT**; most stars + active push; **already has Gmail/Calendar/Outlook agent plugins** — closest tool overlap; JS stack shareable with a TS frontend; RAG+agents in one box.

**Cons:** Node/Prisma world ≠ your Python backend; forking means owning a huge Mintplex product surface; calendar support is agent-plugin level, not battle-tested “meeting prep / confirm / RLS” product; memory model too thin for “personal agent identity.”

### Forkability
High (MIT, popular). Still a **rewrite of your backend** if you make it the root. Prefer: **borrow plugin patterns** or run AnythingLLM as a knowledge workspace beside the butler.

### Paths read
`README.md`, `server/` listing, `server/utils/agents/` (+ `ephemeral.js`, `aibitat/plugins/` listing), docs memories page.

---

## 4. Leon — deep

### Repo meta
| Field | Value |
|---|---|
| Repo | [leon-ai/leon](https://github.com/leon-ai/leon) |
| Default branch studied | **`develop`** (2.0 Developer Preview) |
| License | **MIT** |
| Language | TypeScript (Node ≥24) + Python bridges (`tcp_server`, bridges) |
| Stars | **17,638** |
| Last push | **2026-11-04 09:51 Asia/Singapore** |

README warning (2026-03-29): public docs lag; use `core/context/LEON.md` + `ARCHITECTURE.md`; legacy stable on `master`.

### Positioning
Open-source **personal AI assistant** with tools, context, memory, agentic execution; privacy-aware; local or remote models; computer/browser use; proactive “pulse.”

### Architecture (evidence from `ARCHITECTURE.md` / `LEON.md`, generated 2026-11-02 Asia/Singapore)
**Layout:** `server/` runtime; `apps/web` + `apps/web-legacy`; `aurora/` UI kit; `skills/` (`native/` + `agent/`); `bridges/` Node+Python; `tcp_server/`; `core/context/`.

**Layer model:** `Skills → Actions → Tools → Functions (→ Binaries)`.

**Modes:** `agent` (default loop), `smart` (auto), `controlled` (deterministic native skills).

**Core:** `core/brain/brain.ts`, `llm-duties/react-llm-duty.ts`, `tool-manager/toolkit-registry.ts`, `tool-executor.ts`; turn lifecycle in `core/turn-lifecycle/`.

**Memory:** layered persistent / daily / discussion; `OWNER.md` curated profile; SQLite temp context; **QMD** retrieval with embeddings; experiences as learned `SKILL.md` (cap 196).

**Context:** runtime context files (system, activity, browser, network, workspace, habits…).

**Computer use:** browser toolkit (Browser Use CLI); Cua for GUI apps; optional **Leon Satellite** for remote server → local device tools.

**Channels / clients:** Socket.IO + HTTP/SSE; multi-profile isolation; not Slack/Telegram-first like Letta (more local assistant + Satellite).

**Proactivity:** bounded pulse → `PULSE.md`, ≤1 matter/tick, learns from declines; private self-model/diary.

### Capability boundary
**CAN:** End-to-end personal assistant loop with serious memory/context design; progressive tool loading; desktop/browser automation; multi-profile privacy; agent+controlled hybrid.

**NOT:** Mature public docs yet; contributor onboarding gated (form); not a hosted multi-tenant calendar SaaS; computer-use fragility still applies; smaller community than AnythingLLM/Khoj.

### Pros / cons vs calendar butler
**Pros:** Best **personal-agent architecture writeup** in this set; MIT; memory/context/pulse map directly to “more like Grok Bot beside you”; skill/tool layering is a clean model for your tool registry + confirm cards.

**Cons:** 2.0 still preview — high churn risk as fork base; TS+Node 24 + Python bridges ≠ your current Python API; you’d rewrite calendar OAuth/confirm flows into Leon skills.

### Forkability
MIT and conceptually closest. **High learning value, risky as primary fork** until 2.0 docs stabilize. Prefer **steal ARCHITECTURE patterns** into Omni-Butler.

### Paths read
`README.md` (develop), `core/context/ARCHITECTURE.md`, `core/context/LEON.md`, top-level layout (`server`, `skills`, `bridges`, `apps`, `tcp_server`).

---

## 5. Eigent — short adjacent

| Field | Value |
|---|---|
| Repo | [eigent-ai/eigent](https://github.com/eigent-ai/eigent) |
| License | Apache-2.0 |
| Language | TypeScript (Electron + React) + Python FastAPI backend (CAMEL) |
| Stars | **15,734** |
| Last push | **2026-11-03 23:34 Asia/Singapore** |

**Positioning:** Open-source **Cowork desktop** alternative to Claude Cowork / Codex — multi-agent workforce, browser/terminal toolkits, MCP, skills, local or cloud.

**Boundary:** Desktop productivity automation / parallel agents — **not** a always-on personal second brain with mail/calendar identity. Good inspiration for “workforce” UX; poor root fork for a calendar butler server product.

**Paths:** `README.md`, top-level (`electron/`, `backend/`, `server/`, `src/`).

---

## 6. Open Interpreter — short contrast

| Field | Value |
|---|---|
| Repo | [openinterpreter/openinterpreter](https://github.com/openinterpreter/openinterpreter) (current Rust/Codex-fork line; old Python at community fork `endolith/open-interpreter`) |
| License | Apache-2.0 |
| Language | **Rust** (primary now) |
| Stars | **68,790** |
| Last push | **2026-11-04 09:39 Asia/Singapore** |

**Positioning:** Coding agent / harness emulator for low-cost models; ACP + Codex-protocol compatible; sandbox; optional computer-use QA skill (agent-browser / cua). Explicitly **not** a personal-assistant product with memory/channels/calendar.

**Use as contrast only:** “Local computer/coding agent” ≠ “personal agent / second brain.”

**Paths:** `README.md`.

---

## Side-by-side (deep four)

| | Khoj | Letta Code | AnythingLLM | Leon 2.0 |
|---|---|---|---|---|
| Stars (active code) | 38k | 3.8k (25k on archive name) | **68k** | 18k |
| License | AGPL-3.0 | Apache-2.0 | MIT | MIT |
| Stack | Python/Django | TypeScript | Node/React | TS + Python bridges |
| Core metaphor | Second brain / RAG | Stateful MemFS agent | Local ChatGPT+agents | Personal assistant OS |
| Memory depth | Docs RAG | **Very high** | Shallow facts | **High** layered |
| Channels | Web, Obsidian, Emacs, WA… | Slack/TG/Discord+plugins | Web/Desktop/API | Socket.IO / Satellite |
| Calendar/mail | Weak as product | Not core | **Plugins exist** | Via tools/skills (not GCal-first) |
| Activity | Quiet since Aug 2026 | Very hot | Very hot | Hot on develop |
| Fork as butler root | Poor (AGPL+focus) | Poor (TS harness) | Possible but huge | Tempting but preview |

---

## Recommendation for “是否拉项目开发”

1. **Do not abandon Omni-Butler for a wholesale fork.** Your calendar/mail confirm-card + Python backend is differentiated; none of these replace it cleanly.
2. **Pattern imports (highest ROI):**
   - From **Leon:** memory layers + OWNER profile + proactive pulse + Skills→Tools layering.
   - From **Letta:** channel pairing/routing + permission modes + scheduled heartbeats (if you want chat-app presence).
   - From **AnythingLLM:** study `gmail` / `google-calendar` / `outlook` plugin shapes; optional RAG workspace sidecar.
   - From **Khoj:** only if you need AGPL-acceptable self-hosted RAG and Obsidian sync — else AnythingLLM MIT is easier.
3. **If you insist on standing up a second repo for experiments:** spin **AnythingLLM** (MIT, calendar plugins) or **Leon develop** (personal-agent architecture) as a **lab**, not as production cutover.
4. **Avoid** Open Interpreter / Eigent as the personal-agent foundation; wrong product shape.

---

*Generated 2026-11-04 ~16:48 Asia/Singapore. Evidence from GitHub API + cited docs/READMEs only.*
