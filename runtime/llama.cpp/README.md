# llama.cpp Runtime

Target runtime:

- llama.cpp release: `b9846`
- Build: Windows x64 CUDA 12.4
- GPU verified: `CUDA0: NVIDIA GeForce RTX 5060 Ti`

Required files:

- `llama-mtmd-cli.exe`
- `llama-server.exe`
- `ggml-cuda.dll`
- `cudart64_12.dll`
- `cublas64_12.dll`
- `cublasLt64_12.dll`

Target server command:

```powershell
runtime\llama.cpp\llama-server.exe `
  -m runtime\models\minicpm-v4.6\MiniCPM-V-4_6-F16.gguf `
  --mmproj runtime\models\minicpm-v4.6\mmproj-model-f16.gguf `
  -c 8192 `
  --gpu-layers all `
  --reasoning off `
  --reasoning-format none `
  --reasoning-budget 0 `
  --host 127.0.0.1 `
  --port 18181 `
  --no-webui
```

Single-image CLI validation command:

```powershell
runtime\llama.cpp\llama-mtmd-cli.exe `
  -m runtime\models\minicpm-v4.6\MiniCPM-V-4_6-F16.gguf `
  --mmproj runtime\models\minicpm-v4.6\mmproj-model-f16.gguf `
  -c 8192 `
  --gpu-layers all `
  --image experiments\target_model_samples\ide_errors\current_window.png `
  -f experiments\prompts\analyze_window_v1.txt
```

Notes:

- `llama-server.exe` is the product target because it supports `--reasoning on|off|auto`, `--reasoning-format`; this project keeps reasoning off for structured observation, and avoids reloading the model for every request.
- `llama-mtmd-cli.exe` is only for single-image validation; in this local build it does not accept `--reasoning` / `-rea`.
- FastAPI should resize screenshots before sending them to `llama-server`.
- Current measured result: server startup to `/health` ready is about 11.62s; a 512px image request is about 2.40s.
