# Bark Voice Notes — PLAN

Local, offline speech-to-text page for Tara's MacBook Air (M-series, 16 GB).
Models: Underdog Bark 3.0 (Regular, verbatim, 163 MB) and Underdog Bark 3.0 Clean (drops ums, 180 MB).
Weights live OUTSIDE the repo at ~/Models/underdog-bark/{regular,clean}/ (each has model.bark, bark_loader.py, bark_mlx.py, mlx/*).
Runtime: MLX via mlx-audio (Parakeet graph). Python 3.12 venv at <app>/.venv (mlx==0.32.2 mlx-audio==0.5.3 constriction numpy soundfile soxr).
First load of each variant unpacks model.bark into an fp16 MLX cache folder ~/Models/underdog-bark/<variant>/bark_mlx_fp16 (~6 GB RAM once, then cached).

## Milestones
- M1: done. `bark_engine.py` (load/cache/transcribe both variants, lazy, one at a time) + `transcribe_cli.py` (prints text + timing). Proven on a `say -o` clip.
- M2: done. `server.py` on 127.0.0.1, port from BARK_PORT (default 8099). GET / serves `web/index.html`; other GET paths serve files from `web/` (no traversal). GET /api/status -> {ok, loaded, variants}. POST /api/transcribe?variant=regular|clean|both with a raw audio body -> {results:[{variant, text, seconds, load_seconds, audio_seconds}], filename}.
- M3: written. Mic stays on "Starting mic…" until audio frames arrive, then "Listening…". MLX cache capped at 512 MB after each load. Idle thread unloads models after BARK_IDLE_MIN (default 15) with no transcription. POST /api/quit from 127.0.0.1, Stop link, `Start Bark Voice Notes.command`, `Stop Bark Voice Notes.command`, `setup.sh`, README. No LaunchAgent.
- A1: history store and HTTP API in `server.py`. The page does not use it yet.
- A3: Mac app wrapper in `mac/`. See below.

## A1 history API

Recordings live in `BARK_DATA_DIR` or `~/Library/Application Support/Bark Voice Notes`, at `recordings/<id>/`. Each folder holds the original upload bytes as `audio<ext>` plus `meta.json`. `id` is local time `YYYYMMDD-HHMMSS-` plus 4 hex characters. Meta is written with a temp file and `os.replace`. One lock covers writes. A missing `X-Source` treats `recording.wav` as mic and any other filename as a file.

- `POST /api/transcribe?variant=regular|clean|both[&save=0]` — transcribes as before and, unless `save=0`, stores the upload (`X-Source: mic|file`, `X-Filename`). The success JSON adds `recording` (the meta). If transcription fails, the audio stays and the error JSON includes `id`.
- `GET /api/recordings` — `{"recordings":[{id,name,created,duration,source,variants,preview}]}` newest first. `preview` is the first line of the clean transcript when that variant has been run, else regular, at most 140 characters. Folders with broken meta are skipped and logged once.
- `GET /api/recordings/<id>` — full meta, or 404. Every id must match `^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$`.
- `GET /api/recordings/<id>/audio` — the audio bytes. Content-Type: wav `audio/wav`, m4a `audio/mp4`, mp3 `audio/mpeg`, aiff `audio/aiff`, flac `audio/flac`, webm `audio/webm`, caf `audio/x-caf`. `Accept-Ranges: bytes`. `206` for `bytes=a-b`, `bytes=a-`, and `bytes=-n`. `416` when the range cannot be satisfied.
- `POST /api/recordings/<id>/transcribe?variant=regular|clean` — transcribes the stored audio, writes that variant into meta, returns meta.
- `POST /api/recordings/<id>/rename` — JSON `{"name":"..."}`. The name is trimmed, control characters are removed, and the result must be 1–120 characters. Returns meta.
- `POST /api/recordings/<id>/delete` — moves the whole recording folder to the Trash and returns `{"ok":true}`. When `BARK_TRASH_HELPER` is set, the server runs `[helper, "--trash", folder]` with a 15 second timeout and requires exit 0. Otherwise it `shutil.move`s the folder into `~/.Trash/` as `<name> (Bark) <id>`. Recordings are not deleted in place.

`/api/status`, `/api/quit`, the Origin check on every POST, and static files from `web/` are unchanged. Each transcription updates the idle-unload clock.

## A3 Mac app

Swift package in `mac/` (command-line tools only, no Xcode project), macOS 14+. `mac/install-app.sh` builds `~/Applications/Bark Voice Notes.app`, signs it ad-hoc with a stable identifier (`com.tara.barkvoicenotes`) so the microphone permission survives updates, and registers it with Launch Services. Nothing is installed at login.

The app is one window (1180×780, minimum 820×560) with a WKWebView. On launch it GETs `http://127.0.0.1:8099/api/status`. If that answers, the app uses the server that is already running. Otherwise it starts `Contents/Resources/server/server.py` with `BARK_PYTHON` or `~/Projects/bark-voice-notes/.venv/bin/python`, `PYTHONUNBUFFERED=1`, and `BARK_TRASH_HELPER` set to the app executable. Server stdout and stderr append to `~/Library/Logs/BarkVoiceNotes-server.log`.

Quit (⌘Q or the last window) POSTs `/api/quit` with `Origin: http://127.0.0.1:8099`, then terminates the child process if this app started it. Microphone capture is granted only for host `127.0.0.1`, after the system prompt. The same binary handles `--trash <folder>` via `FileManager.trashItem` for `BARK_TRASH_HELPER`. `NSAllowsLocalNetworking` is set so the web view and the status check can use `http://127.0.0.1`.

## Rules
- NEVER call 127.0.0.1:7777, Osaurus, Ollama, LM Studio.
- No weights, .venv, .env or keys in git.
