"""Local Bark Voice Notes server. Listens on 127.0.0.1 only."""

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import bark_engine

HOST = "127.0.0.1"
DEFAULT_PORT = 8099
MAX_BYTES = 100 * 1024 * 1024
LONG_SECONDS = 120
CHUNK_SECONDS = 60
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac", ".webm", ".caf", ".ogg"}

_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".wav": "audio/wav",
}


class ClientError(Exception):
    pass


def listen_port():
    raw = os.environ.get("BARK_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        raise SystemExit(f"BARK_PORT must be an integer, got {raw!r}")
    if port < 1 or port > 65535:
        raise SystemExit(f"BARK_PORT out of range: {port}")
    return port


def web_file(url_path):
    rel = unquote(url_path).replace("\x00", "").lstrip("/")
    if rel in ("", "."):
        rel = "index.html"
    if os.path.isabs(rel):
        return None
    root = os.path.realpath(WEB_DIR)
    try:
        full = os.path.realpath(os.path.join(root, rel))
    except (OSError, ValueError):
        return None
    if full != root and not full.startswith(root + os.sep):
        return None
    if not os.path.isfile(full):
        return None
    return full


def variants_from_query(query):
    raw = (parse_qs(query).get("variant") or [""])[0].strip().lower()
    if raw == "both":
        return bark_engine.VARIANTS
    if raw in bark_engine.VARIANTS:
        return (raw,)
    raise ClientError("variant must be regular, clean, or both")


def display_name(header):
    name = os.path.basename(unquote(header or "")).replace("\x00", "")
    name = name.replace("\r", "").replace("\n", "").strip()
    if not name or name in (".", ".."):
        return "audio"
    return name[:180]


def check_length(length):
    if length <= 0:
        raise ClientError("empty audio")
    if length > MAX_BYTES:
        raise ClientError("audio is larger than 100 MB")


def _suffix(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in AUDIO_EXT:
        return ext
    return ".bin"


def read_wave_file(path):
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if getattr(data, "size", 0) == 0:
        raise ClientError("audio is empty")
    mono = data.mean(axis=1)
    sr = int(sr)
    if sr <= 0 or len(mono) == 0:
        raise ClientError("audio is empty")
    return mono, sr


def read_any(path):
    try:
        return read_wave_file(path)
    except ClientError:
        raise
    except Exception as first:
        converted = os.path.join(os.path.dirname(path), "out.wav")
        cmd = ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", path, converted]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=180)
        except FileNotFoundError:
            raise ClientError(
                "could not read this audio (soundfile failed and afconvert is unavailable)"
            ) from first
        except subprocess.TimeoutExpired:
            raise ClientError("audio conversion timed out") from first
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
            detail = (detail.splitlines() or [str(first)])[-1][:300]
            raise ClientError("could not read this audio: " + detail) from first
        try:
            return read_wave_file(converted)
        except ClientError:
            raise
        except Exception as exc:
            raise ClientError("could not read this audio: " + str(exc)[:300]) from exc


def transcribe_wave(wave, sr, variant):
    """Bark holds the whole clip in the graph; chunk long audio on a 16 GB Mac."""
    n = len(wave)
    duration = n / float(sr)
    if duration <= LONG_SECONDS:
        return bark_engine.transcribe_array(wave, sr, variant)
    chunk = max(1, int(CHUNK_SECONDS * sr))
    parts = []
    seconds = 0.0
    load_seconds = 0.0
    offset = 0
    while offset < n:
        result = bark_engine.transcribe_array(wave[offset:offset + chunk], sr, variant)
        text = (result.get("text") or "").strip()
        if text:
            parts.append(text)
        seconds += float(result["seconds"])
        load_seconds += float(result["load_seconds"])
        offset += chunk
    return {
        "variant": variant,
        "text": " ".join(parts),
        "seconds": seconds,
        "load_seconds": load_seconds,
        "audio_seconds": duration,
    }


def transcribe_upload(data, filename, variants):
    with tempfile.TemporaryDirectory(prefix="bark-") as folder:
        src = os.path.join(folder, "input" + _suffix(filename))
        with open(src, "wb") as handle:
            handle.write(data)
        wave, sr = read_any(src)
        return [transcribe_wave(wave, sr, variant) for variant in variants]


def warm():
    try:
        bark_engine.load("regular")
    except Exception as exc:
        print("warm regular failed: " + str(exc).splitlines()[0][:300], flush=True)
        return
    print("warmed regular", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "BarkVoiceNotes"

    def log_request(self, code="-", size="-"):
        return

    def _log(self, code, note=""):
        path = self.path.replace("\r", " ").replace("\n", " ")[:200]
        line = f'{self.client_address[0]} "{self.command} {path}" {code}'
        if note:
            line += " " + note.replace("\r", " ").replace("\n", " ")[:300]
        print(line, flush=True)

    def _send(self, code, body, content_type, note=""):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self._log(code, note)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _json(self, code, payload):
        note = payload.get("error", "") if isinstance(payload, dict) else ""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", note)

    def _fail(self, code, message):
        try:
            self._json(code, {"error": message[:500]})
        except Exception:
            print(f"fail {code} {message[:300]}", flush=True)

    def do_GET(self):
        try:
            self._get()
        except Exception as exc:
            self._fail(500, str(exc))

    def _get(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            self._json(200, {
                "ok": True,
                "loaded": bark_engine.loaded(),
                "variants": list(bark_engine.VARIANTS),
            })
            return
        if path == "/favicon.ico":
            self._log(204)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/api/"):
            self._json(404, {"error": "not found"})
            return
        found = web_file(path)
        if found is None:
            self._send(404, "not found\n", "text/plain; charset=utf-8", "not found")
            return
        with open(found, "rb") as handle:
            body = handle.read()
        ext = os.path.splitext(found)[1].lower()
        self._send(200, body, _TYPES.get(ext, "application/octet-stream"))

    def do_POST(self):
        try:
            self._post()
        except ClientError as exc:
            self.close_connection = True
            self._fail(400, str(exc))
        except Exception as exc:
            self._fail(500, str(exc))

    def _post(self):
        path = urlparse(self.path)
        if path.path != "/api/transcribe":
            self._json(404, {"error": "not found"})
            return
        variants = variants_from_query(path.query)
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            raise ClientError("missing Content-Length")
        try:
            length = int(raw_len)
        except ValueError:
            raise ClientError("bad Content-Length")
        check_length(length)
        filename = display_name(self.headers.get("X-Filename"))
        data = self.rfile.read(length)
        if len(data) != length:
            raise ClientError("incomplete audio upload")
        results = transcribe_upload(data, filename, variants)
        self._json(200, {"results": results, "filename": filename})


def main():
    port = listen_port()
    try:
        httpd = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as exc:
        print(
            f"Bark Voice Notes cannot bind {HOST}:{port} ({exc}). "
            "The port is busy or unavailable. Stop the other process or set BARK_PORT.",
            flush=True,
        )
        return 1
    httpd.daemon_threads = True
    threading.Thread(target=warm, name="bark-warm", daemon=True).start()
    print(f"Bark Voice Notes listening on http://{HOST}:{port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
