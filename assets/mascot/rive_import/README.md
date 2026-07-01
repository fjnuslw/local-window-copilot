# Rive Import Kit

This folder is the first Rive-ready import package for the desktop floating assistant.

## Current Rive File Status

- Cloud file: `https://editor.rive.app/file/untitled/2402432`
- File name in Rive: `desktop_mascot_v1`
- Artboard size: `628 x 462`
- Imported assets: all five PNG files in this folder.
- Current implementation: five image layers are aligned on the artboard, and five timelines switch face overlays through opacity keyframes.

Runtime export is currently blocked by the Rive workspace plan. The editor shows `Upgrade to export` for `Export -> For runtime`, so no local `.riv` file has been produced yet.

Until `desktop_mascot_v1.riv` is exported, the floating-window prototype should keep using the PNG sprite fallback in `assets/mascot/composed/`.

## Import Files

- `mascot_base_idle.png`: stable base mascot with idle expression.
- `face_observing_overlay.png`: full-canvas overlay for the observing expression.
- `face_analyzing_overlay.png`: full-canvas overlay for the analyzing expression.
- `face_privacy_overlay.png`: full-canvas overlay for the privacy expression.
- `face_error_overlay.png`: full-canvas overlay for the error expression.

All files use the same `628 x 462` canvas. Import them into Rive at the same position and size.

## Artboard

- Name: `DesktopMascot`
- Size: `628 x 462`
- Background: transparent

## Layer Order

1. `mascot_base_idle`
2. `face_observing_overlay`
3. `face_analyzing_overlay`
4. `face_privacy_overlay`
5. `face_error_overlay`
6. Optional vector glow/ring effects drawn in Rive

Default opacity:

- `mascot_base_idle`: 100%
- all face overlays: 0%

## Animations

Create these animations in Rive. The current Rive file still uses the default timeline names; rename them when the editor allows stable text selection.

- `idle` / current Rive name: `Timeline 1`
  - Mascot group Y: `0 -> -8 -> 0`
  - Duration: `2400ms`
  - Loop: on

- `observing` / current Rive name: `Timeline 2`
  - `face_observing_overlay` opacity: `100%`
  - Mascot group Y: `0 -> -6 -> 0`
  - Optional: add a cyan scan bracket pulse around the face screen
  - Duration: `1800ms`
  - Loop: on

- `analyzing` / current Rive name: `Timeline 3`
  - `face_analyzing_overlay` opacity: `100%`
  - Mascot group rotation: `0 -> -2 -> 2 -> 0`
  - Optional: add a cyan ring rotation behind the thruster
  - Duration: `1200ms`
  - Loop: on

- `privacy` / current Rive name: `Timeline 4`
  - `face_privacy_overlay` opacity: `100%`
  - Mascot group scale: `1 -> 1.025 -> 1`
  - Optional: add a soft green-cyan shield glow
  - Duration: `1600ms`
  - Loop: on

- `error` / current Rive name: `Timeline 5`
  - `face_error_overlay` opacity: `100%`
  - Mascot group X: `0 -> -5 -> 5 -> -3 -> 0`
  - Duration: `500ms`
  - Loop: on

Each state animation should keep the other face overlays at `0%` opacity.

## State Machine

Name: `MascotState`

Input:

- `mode`: number

Mode mapping:

- `0`: idle
- `1`: observing
- `2`: analyzing
- `3`: privacy
- `4`: error

Transitions:

- Entry -> `idle`
- Any State -> `idle` when `mode == 0`
- Any State -> `observing` when `mode == 1`
- Any State -> `analyzing` when `mode == 2`
- Any State -> `privacy` when `mode == 3`
- Any State -> `error` when `mode == 4`

Export file name:

- `desktop_mascot_v1.riv`

Target project path after export:

- `apps/floating-window/public/rive/desktop_mascot_v1.riv`
