# Local Window Copilot Backend

FastAPI local orchestration service for the desktop floating assistant.

Current scope:

- `GET /health`
- `GET /api/assistant/state`
- `POST /api/assistant/state`
- `GET /api/assistant/events`

The backend currently writes the existing desktop state bridge file:

```text
apps/desktop-floating-window/state_bridge.json
```

This keeps the first backend milestone simple: FastAPI can drive the native desktop floating window without changing the desktop renderer yet.

## Run

```powershell
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 18080 --reload
```

## Test

```powershell
cd backend
uv run pytest
```

## Example

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:18080/api/assistant/state `
  -ContentType "application/json" `
  -Body '{"state":"analyzing","reason":"manual-test"}'
```
