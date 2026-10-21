# Bark Voice Notes — PLAN

Local, offline speech-to-text page for Tara's MacBook Air (M-series, 16 GB).
Models: Underdog Bark 3.0 (Regular, verbatim, 163 MB) and Underdog Bark 3.0 Clean (drops ums, 180 MB).
Weights live OUTSIDE the repo at ~/Models/underdog-bark/{regular,clean}/ (each has model.bark, bark_loader.py, bark_mlx.py, mlx/*).
Runtime: MLX via mlx-audio (Parakeet graph). Python 3.12 venv at <app>/.venv (mlx==0.32.2 mlx-audio==0.5.3 constriction numpy soundfile soxr).
First load of each variant unpacks model.bark into an fp16 MLX cache folder ~/Models/underdog-bark/<variant>/bark_mlx_fp16 (~6 GB RAM once, then cached).

## Milestones
- M1: `bark_engine.py` (load/cache/transcribe both variants, lazy, one at a time) + `transcribe_cli.py` (prints text + timing). Proven on a `say -o` clip.
- M2: `server.py` (stdlib http.server or tiny dependency-free) on 127.0.0.1:8099, serves `web/index.html`; POST /api/transcribe?variant=regular|clean with audio body -> JSON {text, variant, seconds, audio_seconds}. Page: hold-to-talk mic (MediaRecorder -> WAV in browser), file drop, Regular/Clean switch, timing readout.
- M3: polish, README, `Start Bark Voice Notes.command` (starts server on demand, opens page; no LaunchAgent), stop instructions.

## Rules
- NEVER call 127.0.0.1:7777, Osaurus, Ollama, LM Studio.
- No weights, .venv, .env or keys in git.
