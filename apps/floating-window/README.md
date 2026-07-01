# Floating Window Prototype

This is the first working UI slice for the local window-aware assistant.

Run it with:

```powershell
npm start
```

The dev server serves the app at `http://127.0.0.1:4173/floating` and exposes the repo-level `assets/` folder so the mascot files are loaded from the same paths that the Tauri WebView can reuse later.

Current scope:

- draggable floating assistant
- layered mascot runtime using local PNG base and face overlays
- idle, observing, analyzing, privacy, and error animations
- state controls for idle, observing, analyzing, privacy, and error
- zero runtime dependencies

Rive status:

- Runtime export is blocked by the current Rive workspace plan.
- This prototype does not depend on `.riv` export.
- If `desktop_mascot_v1.riv` becomes available later, it can replace only the visual renderer while keeping the same `setState(...)` state contract.

Next desktop step:

- wrap this app in a transparent, always-on-top Tauri window after the Rust/Tauri toolchain is installed
