# Desktop Floating Window

This is the first real desktop-window slice for the local window-aware assistant.

It is not a browser page. It opens a borderless, always-on-top transparent Windows floating window using a native Win32 layered window and local mascot PNG layers.

## Current Tech Stack

- Runtime language: Python 3.
- Desktop window: Win32 API through `ctypes`.
- Transparency: per-pixel alpha with `UpdateLayeredWindow`, not chroma-key green-screen transparency.
- Rendering: Pillow RGBA compositing.
- Assets: local PNG base layer plus face overlays from `assets/mascot/rive_import/`.
- State bridge: local JSON file polling through `state_bridge.json`.
- Packaging status: prototype runtime script; not packaged as an installer yet.

## Run

```powershell
python apps\desktop-floating-window\desktop_floating_window.py
```

Or double-click:

```text
apps/desktop-floating-window/start_desktop_window.cmd
```

Controls:

- Drag the mascot area to move the window.
- Click the mascot to cycle states.
- Click the five toolbar buttons to switch states directly.
- Press `Esc` or `Ctrl+Q`, or click the small top-right close button, to exit.

## State Bridge

The window watches:

```text
apps/desktop-floating-window/state_bridge.json
```

Change it with:

```powershell
python apps\desktop-floating-window\set_state.py analyzing
python apps\desktop-floating-window\set_state.py privacy
python apps\desktop-floating-window\set_state.py error
```

This is a temporary local bridge. The final app should expose the same state contract through FastAPI WebSocket/SSE or a Tauri command channel.

## State Contract

- `idle`: assistant is waiting.
- `observing`: screen/window capture is active.
- `analyzing`: MiniCPM-V / llama.cpp inference is running.
- `privacy`: permission or privacy boundary is being shown.
- `error`: local model, capture, or backend failed.
