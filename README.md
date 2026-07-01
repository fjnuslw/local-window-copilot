# Local Window Copilot

A privacy-first local window-aware AI copilot powered by MiniCPM-V, llama.cpp, FastAPI, and a native Windows floating assistant.

This project explores a non-executing desktop AI assistant: it observes the current window, understands the visual and UI context, suggests useful questions, and answers based on that context. It does not click, type, submit forms, delete files, or operate the computer on behalf of the user.

## Current Status

Implemented:

- Native Windows floating assistant in `apps/desktop-floating-window/`
- Borderless always-on-top transparent window
- Per-pixel alpha rendering with Win32 `UpdateLayeredWindow`
- Local mascot rendering with Pillow and PNG layers
- State contract: `idle`, `observing`, `analyzing`, `privacy`, `error`
- Web visual prototype in `apps/floating-window/`
- MiniCPM-V 4.6 F16 and llama.cpp runtime plan
- Project planning docs under `project_plan/`

In progress next:

- FastAPI local state service on `127.0.0.1:18080`
- Window capture service
- Local llama.cpp server integration on `127.0.0.1:18181`
- Redis task state and PostgreSQL behavior logging

## Architecture

```text
Native Windows Floating Assistant
  Python + Win32 + Pillow
  |
  | state events
  v
FastAPI Local Backend
  |
  +-- Window Capture Service
  +-- Privacy Filter
  +-- Context Builder
  +-- ModelRuntimeManager
  |
  +-- Redis task status / cache
  +-- PostgreSQL analysis and event logs
  |
  v
llama.cpp server
  |
  v
MiniCPM-V 4.6 F16
```

## Tech Stack

- Desktop shell: Python 3, Win32 API via `ctypes`, Pillow
- Transparency: Win32 layered window with per-pixel alpha
- Backend target: FastAPI, Pydantic
- Model runtime target: llama.cpp server
- Vision-language model target: MiniCPM-V 4.6 GGUF, F16
- Future engineering layers: Redis, PostgreSQL, SQLAlchemy, Alembic

## Repository Layout

```text
apps/
  desktop-floating-window/    # Native Windows floating assistant
  floating-window/            # Web visual prototype

assets/
  mascot/                     # Local mascot PNG layers and composed states

experiments/
  prompts/                    # Prompt drafts and model experiments

project_plan/                 # Project plan, execution roadmap, deployment plan

runtime/
  README.md
  models/minicpm-v4.6/
    model_manifest.json       # Model manifest only; weights are not committed
  llama.cpp/
    README.md                 # Runtime notes only; binaries are not committed
```

## Run The Desktop Floating Window

```powershell
python apps\desktop-floating-window\desktop_floating_window.py
```

Or double-click:

```text
apps/desktop-floating-window/start_desktop_window.cmd
```

Switch state manually:

```powershell
python apps\desktop-floating-window\set_state.py idle
python apps\desktop-floating-window\set_state.py observing
python apps\desktop-floating-window\set_state.py analyzing
python apps\desktop-floating-window\set_state.py privacy
python apps\desktop-floating-window\set_state.py error
```

## Run The Web Prototype

```powershell
cd apps\floating-window
npm start
```

Then open:

```text
http://127.0.0.1:4173/apps/floating-window/index.html
```

## Model Files

Large model weights and runtime binaries are intentionally excluded from Git.

Expected local files:

```text
runtime/models/minicpm-v4.6/MiniCPM-V-4_6-F16.gguf
runtime/models/minicpm-v4.6/mmproj-model-f16.gguf
runtime/llama.cpp/llama-server.exe
```

The repository keeps only `model_manifest.json` and runtime notes so the public repo stays lightweight.

## Privacy Boundary

The project is intentionally non-executing:

- It does not automate clicks or keyboard input.
- It does not submit forms.
- It does not delete, install, or modify system settings.
- It should default to local inference.
- It should not save raw screenshots or sensitive UI fields by default.

## Roadmap

The immediate next milestone is the FastAPI state service:

```text
GET  /health
GET  /api/assistant/state
POST /api/assistant/state
GET  /api/assistant/events
```

After that:

```text
window capture -> privacy filter -> MiniCPM-V inference -> Redis async state -> PostgreSQL analysis logs
```

See [project_plan/current_execution_status_and_roadmap.md](project_plan/current_execution_status_and_roadmap.md) for the current execution plan.
