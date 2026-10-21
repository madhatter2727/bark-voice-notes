# Bark Voice Notes — PLAN

Local, offline speech-to-text page for Tara's MacBook Air (M-series, 16 GB).
Models: Underdog Bark 3.0 (Regular, verbatim, 163 MB) and Underdog Bark 3.0 Clean (drops ums, 180 MB).
Weights live OUTSIDE the repo at ~/Models/underdog-bark/{regular,clean}/ (each has model.bark, bark_loader.py, bark_mlx.py, mlx/*).
Runtime: MLX via mlx-audio (Parakeet graph). Python 3.12 venv at <app>/.venv (mlx==0.32.2 mlx-audio==0.5.3 constriction numpy soundfile soxr).
First load of each variant unpacks model.bark into an fp16 MLX cache folder ~/Models/underdog-bark/<variant>/bark_mlx_fp16 (~6 GB RAM once, then cached).

## Milestones
- M1: `bark_engine.py` (load/cache/transcribe both variants, lazy, one at a time) + `transcribe_cli.py` (prints text + timing). Proven on a `say -o` clip.
- M2: written. `server.py` on 127.0.0.1, port from BARK_PORT (default 8099). GET / serves `web/index.html`; other GET paths serve files from `web/` (no traversal). GET /api/status -> {ok, loaded, variants}. POST /api/transcribe?variant=regular|clean|both with a raw audio body -> {results:[{variant, text, seconds, load_seconds, audio_seconds}], filename}.
- M3: polish, README, `Start Bark Voice Notes.command` (starts server on demand, opens page; no LaunchAgent), stop instructions.

## Rules
- NEVER call 127.0.0.1:7777, Osaurus, Ollama, LM Studio.
- No weights, .venv, .env or keys in git.
