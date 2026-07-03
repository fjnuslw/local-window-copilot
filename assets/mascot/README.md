# Mascot Assets

This folder contains the original floating AI assistant mascot assets.

Use `rive_import/` for the current floating-window mascot runtime. It contains one aligned base PNG and four aligned face-overlay PNGs on the same `628 x 462` canvas, so the frontend can switch states without relying on Rive export.

Use `composed/mascot_*.png` only as archived full-sprite assets. The current prototype uses the layer-based `rive_import/` files.

Use `ready_alpha/` for future finer-grained layer-level product work. These PNG files have real alpha channels and preserve the white shell highlights.

Do not use `transparent/` for final UI or Rive import. It was an early background-removal pass and can damage white highlights.

## Folders

- Root PNG files: original generated assets with white RGB backgrounds.
- `composed/`: archived full mascot sprites, including idle, observing, analyzing, privacy, and error states.
- `rive_import/`: current aligned import kit and local layered runtime assets.
- `ready_alpha/`: preferred alpha PNG assets for frontend and Rive work.
- `transparent/`: local experimental alpha pass, ignored in Git because it can damage white highlights and is not part of the product path.

## Recommended Layer Order

1. `hover_ring`
2. `bottom_thruster`
3. `side_wing_left`
4. `side_wing_right`
5. `body_shell_front`
6. `face_screen`
7. `eye_left`
8. `eye_right`
9. `mouth_smile`
10. `top_status_light`

## Face States

- `idle`: compose `face_screen`, `eye_left`, `eye_right`, and `mouth_smile`.
- `observing`: swap to `face_observing`.
- `analyzing`: swap to `face_analyzing`.
- `privacy`: swap to `face_privacy`.
- `error`: swap to `face_error`.

The machine-readable integration map is in `mascot_manifest.json`.

## Rive Export Status

Rive runtime export is currently blocked by the workspace plan, so the project should not depend on `.riv` files for the main implementation path. The frontend now uses the same art assets directly through HTML/CSS/JS, with state switching controlled by `data-state`.
