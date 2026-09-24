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

### Your screen

Share your screen and ask *"What's on my screen?"* — Srilatha looks and describes it briefly. Vision is strictly observational: she never acts on what she sees without your spoken instruction.

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
| `frontend/` | Next.js voice frontend (LiveKit components, Tailwind, shadcn/ui) |

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
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Google realtime model access |

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

---

## Testing and quality

| Layer | Command | Coverage |
|---|---|---|
| **Unit & behaviour tests** | `uv run pytest` | **423 passed, 1 skipped** — resolution, security policy, confirmation flow, every tool |
| **Lint & format** | `uv run ruff check .` · `uv run ruff format --check .` | Clean |
| **Conversation simulations** | `lk agent simulate --scenarios scenarios.yaml` | Multi-turn dialogues judged end-to-end ([scenarios.yaml](scenarios.yaml)) |
| **CI** | GitHub Actions | `ruff.yml` on pushes · `simulations.yml` on merges to `main` |

Security-sensitive behaviour is pinned with test-first development: the confirmation flow, protected paths, traversal and junction refusals, recycle-by-default, allowlist enforcement, and adaptive resolution each have dedicated tests that fail loudly if weakened.

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
│   └── __init__.py
├── frontend/               # Next.js voice UI
├── tests/                  # 423 pytest checks
├── scenarios.yaml          # conversation simulations
├── Dockerfile              # production deployment
├── .env.example            # environment template (real keys stay local)
└── AGENTS.md               # guidance for coding agents
```

---

## Deploying

A production-ready [Dockerfile](Dockerfile) is included. Deploy to LiveKit Cloud or any container host — see the [deploying to production](https://docs.livekit.io/deploy/agents/) guide. Telephony and additional frontends are supported by the same agent; see [frontends & telephony](https://docs.livekit.io/frontends/).

---

## License

MIT — see [LICENSE](LICENSE).

Built on the [LiveKit agent-starter-python](https://github.com/livekit/agent-starter-python) template, with Windows PC control, the confirmation security model, multilingual butler persona, and browser automation developed for Srilatha.
