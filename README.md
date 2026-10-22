# Bark Voice Notes

Offline speech-to-text for this Mac. Hold the button, talk, and the text shows up on the page. Audio stays on the machine.

## Mac app

Open **Bark Voice Notes** from `~/Applications` or Spotlight.

The first time you hold the talk button, macOS asks for the microphone. Click Allow. It asks once.

Quit the app to stop the server and the models. Nothing starts at login.

Recordings are stored in `~/Library/Application Support/Bark Voice Notes/recordings`. Deleted recordings go to the Trash.

Install or update the app from this folder:

```bash
mac/install-app.sh
```

`mac/install-app.sh --open` installs it and opens it.

## Browser

The `.command` scripts are the browser option. They use the same local server.

Two local models:

- **Regular** is Underdog Bark 3.0. It writes what you said, including “um” and “uh”.
- **Clean** is Underdog Bark 3.0 Clean. It drops those filler words.

## Open it in a browser

Double-click `Start Bark Voice Notes.command`.

If it is already running, open [http://127.0.0.1:8099/](http://127.0.0.1:8099/).

The first time you hold the button, the browser asks for the microphone. Click Allow. The button says “Starting mic…” until sound is actually coming in, then “Listening…”. Talk after it turns red.

## A checked clip

Spoken with macOS `say` (8.1 s): “Um, so, I think, uh, we should, you know, meet on Thursday at three p.m. to go over the, um, the budget numbers.”

| Model | Text | Time |
| --- | --- | --- |
| Regular | um so i think uh we should you know meet on thursday at three pm to go over the uh um the budget numbers | 0.15–0.23 s |
| Clean | so I think, we should, meet on Thursday at three p.m. to go over the, the budget numbers. | 0.16–0.29 s |

The first load after a model has been unpacked takes about 0.4–0.8 s. With both models loaded, the server uses about 3.7 GB.

## Stop

Quit the Mac app if that is what you opened. That stops the server and the models.

From the browser:

- Click **Stop server** at the bottom of the page.
- Or double-click `Stop Bark Voice Notes.command`.
- Or in Terminal: `kill $(lsof -tiTCP:8099 -sTCP:LISTEN)`

Nothing starts at login. There is no LaunchAgent. The server only listens on 127.0.0.1 and refuses requests from other websites.

## Memory

Each loaded model is about 1.3 GB. After 15 minutes with no transcription the server unloads them. The next note loads them again (about a second). Set `BARK_IDLE_MIN` to change that wait.

The first time a model is unpacked, memory can peak around 6.5 GB, once. After that the unpacked copy stays in `~/Models/underdog-bark/<variant>/bark_mlx_fp16`.

## Files

- `mac/install-app.sh` — build the Mac app into `~/Applications`
- `Start Bark Voice Notes.command` — start the server if needed, then open the page in a browser
- `Stop Bark Voice Notes.command` — stop it
- `setup.sh` — create `.venv` (Python 3.12), install the pinned packages, download both models
- `server.py` — the page and the API, on 127.0.0.1 port 8099 (`BARK_PORT` to change the port)
- `bark_engine.py` — load a model and transcribe
- `transcribe_cli.py` — transcribe a file from the terminal
- `web/index.html` — the page
- `tests/page_test.py` — browser check
- `logs/server.log` — server output after you use the Start script

Weights live in `~/Models/underdog-bark` (`regular` and `clean`). They are never in git. Neither is `.venv`.

## Models and licence

Underdog Bark 3.0 and Underdog Bark 3.0 Clean are CC-BY-4.0. They are based on NVIDIA parakeet-tdt-0.6b-v3. Credit Underdog and NVIDIA if you share something made with these models.

## First-time setup

You need `uv` and the Hugging Face `hf` command.

```bash
./setup.sh
```

Then double-click `Start Bark Voice Notes.command`, or install the Mac app with `mac/install-app.sh`.

## Page test

With the server running, on the Mac:

```bash
.venv/bin/python tests/page_test.py testclips/clip.wav out.png
```

It needs Playwright in `.venv` (`uv pip install --python .venv/bin/python playwright`; it uses your installed Google Chrome, no extra browser download). It opens Chrome with a throwaway profile, feeds it the wav as a fake microphone, holds the talk button for Regular, Clean, and a file upload of both, and writes a screenshot.
