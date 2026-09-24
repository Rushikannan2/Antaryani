<a href="https://livekit.io/">
  <img src="./.github/assets/livekit-mark.png" alt="LiveKit" width="96" height="96">
</a>

# Srilatha

**The personal voice assistant of Rushi Sir** — a production-grade Windows voice AI that speaks like a refined butler and works like a power user. Files, folders, applications, and the entire web, controlled entirely by voice, wrapped in a hardened safety model that never trusts, never guesses, and never overwrites.

[![ruff](https://github.com/Rushikannan2/Antaryani/actions/workflows/ruff.yml/badge.svg)](https://github.com/Rushikannan2/Antaryani/actions/workflows/ruff.yml)
[![simulations](https://github.com/Rushikannan2/Antaryani/actions/workflows/simulations.yml/badge.svg)](https://github.com/Rushikannan2/Antaryani/actions/workflows/simulations.yml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Built with LiveKit](https://img.shields.io/badge/Built%20with-LiveKit-7B2BF2)](https://livekit.io/)

> *"Good day, Rushi Sir. Srilatha is at your service, in English or any language you prefer."*

---

## What Srilatha can do

### Talk like a person, in your language

- Answers to **Srilatha**, always addresses you as **Rushi Sir**, and never confuses the two (never "Jarvis", never a wrong name).
- Speaks **every language you use**: Hindi, Tamil, Telugu, Bengali, Marathi, Kannada, Malayalam, Gujarati, Spanish, French, German, Portuguese, Italian, Russian, Japanese, Chinese, Korean, Arabic, Turkish, and more — detecting your language per sentence and following code-mixed speech naturally.
- A refined butler personality: respectful and confident, with friendly wit when the moment fits — never robotic, never repetitive.

### Files and folders — the complete Windows file manager by voice

| Group | Tools | What you can say |
|---|---|---|
| **Explore** | `list_directory`, `search_files`, `get_file_info`, `file_exists`, `folder_exists`, `inspect_tree` | *"What's in Downloads?"*, *"Find my resume"*, *"Search for *.pdf in Projects"*, *"How big is this folder?"* |
| **Read** | `read_file` | *"Read this file"* — text and code, plus built-in extraction of **Word, PowerPoint, and PDF** documents |
| **Create & edit** | `create_file`, `create_folder`, `edit_file` | *"Create a folder called Invoices on the Desktop"*, *"Replace this line in my file"* |
| **Organize** | `rename_path`, `move_path`, `copy_path`, `recycle_path`, `delete_path` | *"Rename Rushi to Kannan"*, *"Move it to Documents"*, *"Delete it"* |
| **Open** | `open_path`, `launch_application` | *"Open this PDF"*, *"Open VS Code"* |

Highlights:

- **Adaptive path resolution — never a fixed list.** Standard folders are discovered live from Windows' `User Shell Folders` registry, then matched against folders that actually exist in the current location and your profile. `"Downloads\Rushi"` always means the real Downloads; so does `"download\rushi"`, `"my documents\a.txt"`, or `"photos\cat.png"`. Your own folders — `D:\Learn2Design2026` and any other — resolve the same way.
- **Remembers your context.** *"This file"*, *"that folder"*, *"previous file"*, *"the folder we created"*, or a bare *"rename it"* resolve to the item you just used.
- **Asks, never guesses.** If a name matches several items, you get the candidates back and a short question — never a coin flip.
- **Honest listings.** Folders are listed before files and large listings are flagged `truncated`, with search as the mandated next step before anything is called missing.

### Applications — allowlisted, never arbitrary

Srilatha launches desktop applications **by name only**, from fixed install locations:

> Google Chrome · Microsoft Edge · VS Code · Microsoft Word · PowerPoint · Excel · Notepad · Paint · Calculator · File Explorer

Paths, arguments, scripts, and unknown programs are refused outright (`UNKNOWN_APP`), and a missing install is reported honestly (`APP_MISSING`). A model-supplied path can never become a launched process.

### The web — a full browser under voice control

- **Open & navigate**: `open_url`, `go_back`, `go_forward`, `refresh`, `search_the_web` (DuckDuckGo), multi-tab control (`list_tabs`, `new_tab`, `switch_tab`, `close_tab`).
- **Read**: `read_page`, `inspect_page`, `get_page_state`, `wait_for_content`, `take_screenshot`.
- **Interact**: `click`, `double_click`, `hover`, `type_text`, `clear_field`, `select_option`, `toggle_checkbox`, `scroll`, `press_key`, `submit_form`.
- **Guarded actions**: consequential controls (send, submit, buy, delete, pay, agree…) require explicit spoken confirmation plus the six-digit code before `confirm_browser_action` will proceed.

### Your screen and your PC's vitals

- **Look**: Share your screen and ask *"What's on my screen?"* — Srilatha looks and describes it briefly. Vision is strictly observational: she never acts on what she sees without your spoken instruction.
- **Capture**: *"Take a screenshot"* saves a verified, timestamped PNG of the real desktop to `Pictures\Srilatha` — a new file every time, never an overwrite, never a success claimed without a non-empty file to back it.
- **Record**: *"Start recording"*, *"Pause"*, *"Resume"*, *"Stop recording"* drive a real state-machine screen recorder (pure Python, no shell, no ffmpeg) writing to `Videos\Srilatha`. A casual *"stop"* in ordinary conversation is never treated as a recording command.
- **Measure**: *"What's my battery?"*, *"How much RAM am I using?"*, *"What's my CPU usage?"*, *"How much storage is left?"*, *"What time is it?"* read the live machine through psutil and NVML — and say *unavailable* plainly when a sensor isn't there, never an invented number.

### The personal dashboard

The frontend is a full **Srilatha Personal AI Assistant** dashboard, not just a talk button: live CPU/RAM/GPU/storage/battery/network cards, screenshot and recording controls, recent sessions from the local SQLite history, a sanitized activity feed, quick actions, and the security-status indicator — all wrapped around the voice session.

- Everything on it is **real**: panels poll a local aiohttp API the agent serves on `127.0.0.1:8787`, reached through a same-origin `/api/dashboard` rewrite (localhost only — no CORS, no secrets), backed by the *same* services the voice tools use: one source of truth, no mock data.
- The confirmation banner's presence mirrors to the dashboard as an on/off indicator only; the six-digit code itself never leaves your screen and is never written to localStorage.

The local dashboard also exposes real, verified capture controls:

- Say *"Take a screenshot"*, *"Capture the screen"*, or *"Screenshot this"* to save a timestamped PNG under `Pictures/Srilatha`.
- Say *"Start recording"*, *"Pause"*, *"Resume"*, or *"Stop recording"* to control the verified AVI recorder under `Videos/Srilatha`.
- Ask for CPU, RAM, GPU, storage, battery, network, time, or overall system status. Unavailable GPU/battery values are shown honestly as unavailable.
- **Say *"Camera on"* or *"Camera off"*** to toggle the local video track — or use the camera button (Video / VideoOff icons) beside the chat input. When the camera is on, a small mirrored self-view with a red **Live** badge appears in the voice workspace; when off, it shows a clean "Camera off" state. The LiveKit session respects the camera state.

The dashboard is a local command center, not a second Windows file manager. File moves, protected paths, ambiguity handling, and six-digit confirmations continue through the existing security layer.

### Cross-domain workflows

Multi-step requests run as one continuous workflow across domains:

> *"Download this PDF and put it in my Research folder"* — reach the page with the browser, then find the file in Downloads and move it with the Windows tools.

---

## How safety works

Srilatha's PC access is built on the principle that **the model proposes, the policy disposes** — every request is re-validated by an independent policy layer, and destructive actions require a human in the loop.

| Control | Behaviour |
|---|---|
| **Six-digit confirmation flow** | Destructive or irreversible actions are *staged* with a one-time token. A six-digit code is shown **on your screen only** — never to the model. You read it back; the tool verifies token + code + exact operation/source/destination, with a **120 s TTL**, **3 attempts max**, and **single use**. |
| **Recycle by default** | Deletes go to the Recycle Bin (`recycle_path`); permanent deletion and overwriting an existing file require your clear, explicit confirmation. A `CONFLICT` is never "resolved" by overwriting. |
| **Protected locations** | Drive roots and system folders (`Windows`, `Program Files`, and friends) are refused on **every** drive, for reads-that-consume and writes alike — enforced again at confirmation time. |
| **Path hardening** | `..` traversal is normalized, junction/symlink escapes are resolved and re-checked, extended-length prefixes are handled, and executable-like files (`.exe`, `.bat`, `.ps1`, …) refuse to open. |
| **No shell, ever** | The model has no cmd, PowerShell, subprocess, `eval`, or arbitrary-executable tools. Nothing it says can become a command line. |
| **Prompt-injection hygiene** | Filenames, folder listings, search results, and page text are *data, never instructions*. |
| **Confirmation on the web too** | The same six-digit discipline gates consequential browser actions. |
| **Staged cancellations** | *"Stop"*, *"never mind"* mid-task withdraws the staged action via `cancel_windows_action` — nothing changes. |

---

## Architecture

| Module | Responsibility |
|---|---|
| `src/agent.py` | Entrypoint: agent definition, session wiring, confirmation publisher |
| `src/prompts.py` | Srilatha's identity, personality, language rules, and capability guidance |
| `src/windows_tools.py` | The 19 Windows tools, session context, and disambiguation |
| `src/windows_fs.py` | Filesystem operations, adaptive path resolution, application allowlist |
| `src/windows_security.py` | The independent policy layer: risk, protected paths, confirmation manager |
| `src/confirmation.py` | Six-digit code generation, verification, TTL, attempt limits |
| `src/browser.py` | Playwright browser lifecycle and low-level page control |
| `src/tools.py` | Browser/web/vision tool surface with confirmation gating |
| `src/system_metrics.py` | Sanitized psutil/Win32/NVML metrics with unavailable fallbacks |
| `src/screen_capture.py` | Verified native desktop screenshot capture and controlled artifact paths |
| `src/screen_recording.py` | Strict recording state machine and pure-Python AVI encoder |
| `src/session_history.py` / `src/activity.py` | Local SQLite history and sanitized recent activity |
| `src/dashboard_api.py` / `src/system_tools.py` | Shared local dashboard API and model-facing system tools |
| `frontend/` | Srilatha command-center UI (LiveKit components, Tailwind, shadcn/ui) |

**Stack:** [LiveKit Agents](https://github.com/livekit/agents) for Python · **Gemini 3.1 Flash Live** end-to-end realtime speech (speech in and voice out handled by the model) · [AI Coustics](https://ai-coustics.com/) audio enhancement · [Playwright](https://playwright.dev/) browser control · Windows 10/11 for the PC-control domain.

---

## Quick start

### Prerequisites

- **Windows 10/11** with Python **3.11+**
- [uv](https://docs.astral.sh/uv/)
- [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) **2.15+** — `winget install LiveKit.LiveKitCLI`
- A [LiveKit Cloud](https://cloud.livekit.io/) project and a Google Gemini API key
- Node.js **24.x** with [pnpm](https://pnpm.io/) (for the frontend)

### Install

```console
git clone https://github.com/Rushikannan2/Antaryani.git
cd Antaryani
uv sync
```

Copy the environment template and fill it in (`.env.local` is git-ignored — never commit it):

```console
copy .env.example .env.local
```

| Variable | Purpose |
|---|---|
| `LIVEKIT_URL` | Your LiveKit Cloud WebSocket URL (`wss://…`) |
| `LIVEKIT_API_KEY` | Project API key |
| `LIVEKIT_API_SECRET` | Project API secret |
| `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) | Google realtime model access — both names are accepted and resolved to one key |

Credential loading is handled by [`src/env_config.py`](src/env_config.py), which follows one simple rule so editing the file always takes effect:

- **Credentials (`GOOGLE_API_KEY`, `GEMINI_API_KEY`, `LIVEKIT_*`) come from the files, not the shell:** `.env.local` wins over `.env`, and both win over a stale value left in your shell — rotating a key in `.env.local` never requires unsetting anything first.
- **`GOOGLE_API_KEY` and `GEMINI_API_KEY` are aliases:** either name works; the value is written to both so the Google plugin (which reads `GOOGLE_API_KEY`) always sees it. If both are set to different values, `GOOGLE_API_KEY` wins and a warning is logged.
- **Duplicated lines are safe:** if the same key appears twice in a file, the last value wins.
- **Copy/paste is forgiving:** wrapping quotes and stray whitespace (including a key line-wrapped mid-value) are stripped automatically.
- **Both key formats are valid:** Google AI Studio now issues `AQ.…` auth keys; legacy `AIza…` keys are still accepted. A missing or malformed key logs a warning at startup — the warning names the expected prefixes but never prints your key.

You can pull the LiveKit values straight from the cloud instead of copying them by hand:

```console
lk cloud auth
lk app env --write --destination .env.local
```

### Run

```console
lk agent dev      # development: hot reload + debug logging (recommended)
lk agent console  # talk to her in the terminal
lk agent start    # production mode
```

Then open the frontend:

```console
cd frontend
pnpm install
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000) — the voice session and the personal dashboard share the page, and the panels light up once the agent is running.

The agent process serves the local dashboard API on `127.0.0.1:8787` from worker boot — before the first call — and Next.js rewrites `/api/dashboard/*` to it. Set `SRILATHA_DASHBOARD_AUTOSTART=0` to disable the boot-time server, or run the API separately for dashboard development:

```console
uv run python src/dashboard_api.py
```

Set `SRILATHA_DASHBOARD_PORT` to choose another local port. The API is bound to localhost; it never forwards LiveKit, Gemini, or Windows credentials to the browser. Session history is stored locally in the Srilatha SQLite profile and is sanitized before it is written.

### Troubleshooting

**Frontend build fails with `ENOENT: ... frontend/.next/...` (missing `app-build-manifest.json`, Browserslist data, etc.)** — a `pnpm dev` server (Turbopack) is writing to the same `frontend/.next` directory that `pnpm build` is trying to write. Stop the dev server, clear the stale output, then build cleanly:

```console
# stop `pnpm dev` first (Ctrl+C in its terminal)
Remove-Item -Recurse -Force frontend\.next
cd frontend
pnpm build
```

Never run `pnpm build` while `pnpm dev` is running.

**`ECONNREFUSED` on `/api/dashboard/*`** — the local dashboard API on `127.0.0.1:8787` is not up. It autostarts at worker boot; for frontend-only work start it directly with `uv run python src/dashboard_api.py`.

**`SyntaxError: Unexpected end of JSON input` from `POST /api/token`** — the token endpoint now reads the raw request body first: an empty body is valid and simply means "no room configuration" (the route issues a token with defaults), malformed JSON returns `400` instead of crashing, and every error path returns a response. If you still see this on an older checkout, pull the latest code.

**Rotated the Google API key but the agent still uses the old one** — it can't anymore: `src/env_config.py` loads credentials from `.env.local` (then `.env`) with file-wins precedence, aliases `GEMINI_API_KEY` onto `GOOGLE_API_KEY`, resolves duplicated lines to the last value, and strips pasted whitespace/quotes. Restart the agent after editing the file — environment variables are read once at boot. A warning at startup tells you if the key is missing or doesn't start with `AQ.`/`AIza`; the warning never includes the key itself.

**`The room connection was not established within 10 seconds after calling job_entry`** — the entrypoint calls `ctx.connect()` right after local prep and *before* `AgentSession.start()` (pinned by `tests/test_agent_entrypoint.py`), so on a healthy network the connection is established well inside the 10-second window. If the warning still appears, the room connection itself is slow — usually `wait_pc_connection timed out`, meaning ICE/UDP is being blocked (corporate firewall, VPN, UDP-restricted network) or the LiveKit Cloud region is far away. Those are network-environment issues, not agent bugs: confirm UDP is allowed and retry.

Other start-up log lines — `ssl.create_default_context` blocking, `ai_coustics` FFI initialization, `build_legacy_openai_schema` deprecation, and `no sample` audio warmup — come from the LiveKit, audio, and browser dependencies and are harmless.

---

## Testing and quality

| Layer | Command | Coverage |
|---|---|---|
| **Unit & behaviour tests** | `uv run pytest` | **528 passed, 1 skipped** — resolution, security policy, confirmation flow, every tool, metrics sanitization, the recorder state machine, the dashboard API, boot-time dashboard autostart, credential/env loading precedence, and the room-connect-before-session-start ordering |
| **Lint & format** | `uv run ruff check .` · `uv run ruff format --check .` | Clean |
| **Conversation simulations** | `lk agent simulate --scenarios scenarios.yaml` | Multi-turn dialogues judged end-to-end ([scenarios.yaml](scenarios.yaml)) |
| **CI** | GitHub Actions | `ruff.yml` on pushes · `simulations.yml` on merges to `main` |

Security-sensitive behaviour is pinned with test-first development: the confirmation flow, protected paths, traversal and junction refusals, recycle-by-default, allowlist enforcement, and adaptive resolution each have dedicated tests that fail loudly if weakened. The dashboard layer is held to the same standard: metrics sanitize instead of inventing, screenshots and recordings verify their files and never overwrite, media endpoints refuse traversal, and session history redacts six-digit codes and key material before anything is written.

For coding-agent workflows (Claude Code, Cursor, Codex, OpenCode), see [AGENTS.md](AGENTS.md) and the [LiveKit docs CLI/MCP](https://docs.livekit.io/reference/developer-tools/docs-mcp/).

---

## Project structure

```text
Antaryani/
├── src/
│   ├── agent.py            # entrypoint
│   ├── prompts.py          # personality, languages, capability guidance
│   ├── windows_tools.py    # Windows tool surface + session context
│   ├── windows_fs.py       # filesystem ops, adaptive paths, app allowlist
│   ├── windows_security.py # policy layer: risk, protection, confirmations
│   ├── confirmation.py     # six-digit codes, TTL, attempts
│   ├── browser.py          # Playwright engine
│   ├── tools.py            # web/vision tools + gating
│   ├── system_metrics.py   # live local telemetry
│   ├── screen_capture.py   # verified screenshots
│   ├── screen_recording.py # recording state machine/AVI
│   ├── session_history.py  # local SQLite history
│   ├── activity.py         # sanitized activity ring
│   ├── dashboard_api.py    # localhost dashboard API
│   ├── env_config.py       # .env loading: file-wins credentials, aliases, validation
│   ├── system_tools.py     # model-facing system controls
│   ├── services.py         # shared singletons: history/activity/recorder
│   ├── safe_files.py       # collision-free artifact naming
│   └── __init__.py
├── frontend/               # Next.js Srilatha command center
├── tests/                  # 528 pytest checks
├── scenarios.yaml          # conversation simulations
├── Dockerfile              # production deployment
├── .env.example            # environment template (real keys stay local)
└── AGENTS.md               # guidance for coding agents
```

---

## Deploying

A production-ready [Dockerfile](Dockerfile) is included. Deploy to LiveKit Cloud or any container host — see the [deploying to production](https://docs.livekit.io/deploy/agents/) guide. Telephony and additional frontends are supported by the same agent; see [frontends & telephony](https://docs.livekit.io/frontends/).

---

## Attribution

Srilatha was developed by V T Rushikannan as a personal voice-driven Windows AI assistant.

The project is built on the LiveKit agent-starter-python template. LiveKit provides the underlying agent framework/infrastructure; the Srilatha-specific Windows computer-control layer, security and confirmation model, browser automation, multilingual butler behavior, testing, and application logic were developed for this project.

---

## License

MIT — see [LICENSE](LICENSE).

Built on the [LiveKit agent-starter-python](https://github.com/livekit/agent-starter-python) template, with Windows PC control, the confirmation security model, multilingual butler persona, and browser automation developed for Srilatha.
