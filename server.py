"""Local Bark Voice Notes server. Listens on 127.0.0.1 only."""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HOST = "127.0.0.1"
DEFAULT_PORT = 8099
MAX_BYTES = 100 * 1024 * 1024
LONG_SECONDS = 120
CHUNK_SECONDS = 60
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac", ".webm", ".caf", ".ogg"}
VARIANTS = ("regular", "clean")
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
_REC_RE = re.compile(
    r"^/api/recordings/([0-9]{8}-[0-9]{6}-[0-9a-f]{4})(?:/(audio|transcribe|rename|delete))?$"
)
_AUDIO_NAME = re.compile(r"^audio\.[A-Za-z0-9]{1,8}$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

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

_AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".caf": "audio/x-caf",
    ".ogg": "audio/ogg",
}

_store_lock = threading.Lock()
_bad_lock = threading.Lock()
_bad_logged = set()


class ClientError(Exception):
    pass


class NotFound(Exception):
    pass


def _engine():
    import bark_engine
    return bark_engine


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
        return VARIANTS
    if raw in VARIANTS:
        return (raw,)
    raise ClientError("variant must be regular, clean, or both")


def one_variant(query):
    raw = (parse_qs(query).get("variant") or [""])[0].strip().lower()
    if raw in VARIANTS:
        return raw
    raise ClientError("variant must be regular or clean")


def wants_save(query):
    raw = (parse_qs(query).get("save") or ["1"])[0].strip()
    return raw != "0"


def display_name(header):
    name = os.path.basename(unquote(header or "")).replace("\x00", "")
    name = name.replace("\r", "").replace("\n", "").strip()
    if not name or name in (".", ".."):
        return "audio"
    return name[:180]


def upload_source(headers, filename):
    raw = (headers.get("X-Source") or "").strip().lower()
    if raw in ("mic", "file"):
        return raw
    if raw:
        raise ClientError("X-Source must be mic or file")
    if filename.lower() == "recording.wav":
        return "mic"
    return "file"


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
        with tempfile.TemporaryDirectory(prefix="bark-conv-") as folder:
            converted = os.path.join(folder, "out.wav")
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
    engine = _engine()
    if duration <= LONG_SECONDS:
        return engine.transcribe_array(wave, sr, variant)
    chunk = max(1, int(CHUNK_SECONDS * sr))
    parts = []
    seconds = 0.0
    load_seconds = 0.0
    offset = 0
    while offset < n:
        result = engine.transcribe_array(wave[offset:offset + chunk], sr, variant)
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


def data_dir():
    raw = os.environ.get("BARK_DATA_DIR", "").strip()
    if not raw:
        raw = "~/Library/Application Support/Bark Voice Notes"
    return os.path.expanduser(raw)


def recordings_root():
    path = os.path.join(data_dir(), "recordings")
    os.makedirs(path, exist_ok=True)
    return path


def local_now():
    return datetime.now().astimezone()


def iso_local(now):
    if now.tzinfo is None:
        now = now.astimezone()
    return now.isoformat(timespec="seconds")


def new_id(now):
    return now.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def _strip_controls(text):
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cc")


def default_name(source, filename, now):
    if source == "mic":
        hour = now.hour % 12 or 12
        ampm = "AM" if now.hour < 12 else "PM"
        return f"Voice note {_MONTHS[now.month - 1]} {now.day}, {hour}:{now.minute:02d} {ampm}"
    stem = _strip_controls(os.path.splitext(filename)[0]).strip()
    return stem or "audio"


def clean_name(name):
    if not isinstance(name, str):
        raise ClientError("name must be a string")
    cleaned = _strip_controls(name).strip()
    if not cleaned or len(cleaned) > 120:
        raise ClientError("name must be 1-120 characters")
    return cleaned


def recording_folder(rec_id):
    if not isinstance(rec_id, str) or not ID_RE.fullmatch(rec_id):
        return None
    root = os.path.join(data_dir(), "recordings")
    folder = os.path.join(root, rec_id)
    if os.path.islink(folder):
        return None
    if os.path.dirname(os.path.realpath(folder)) != os.path.realpath(root):
        return None
    return folder


def _log_bad(rec_id, reason):
    with _bad_lock:
        if rec_id in _bad_logged:
            return
        _bad_logged.add(rec_id)
    print(f"skip recording {rec_id}: {reason}", flush=True)


def _schema_error(meta, rec_id):
    if not isinstance(meta, dict):
        return "meta is not an object"
    if meta.get("id") != rec_id:
        return "id mismatch"
    for key in ("name", "created", "source", "filename", "audio"):
        if not isinstance(meta.get(key), str) or not meta.get(key):
            return "missing " + key
    if meta["source"] not in ("mic", "file"):
        return "bad source"
    duration = meta.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return "bad duration"
    if not _AUDIO_NAME.fullmatch(meta["audio"]):
        return "bad audio name"
    transcripts = meta.get("transcripts", {})
    if not isinstance(transcripts, dict):
        return "bad transcripts"
    for key, entry in transcripts.items():
        if key not in VARIANTS:
            return "bad variant"
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            return "bad transcript"
        seconds = entry.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return "bad transcript seconds"
        if not isinstance(entry.get("at"), str) or not entry.get("at"):
            return "bad transcript at"
    return None


def _load_meta(folder, rec_id):
    path = os.path.join(folder, "meta.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError):
        return None, "broken meta"
    err = _schema_error(meta, rec_id)
    if err:
        return None, err
    return meta, None


def _write_meta_unlocked(folder, meta):
    dest = os.path.join(folder, "meta.json")
    tmp = os.path.join(folder, ".meta.json.tmp")
    payload = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, dest)


def _audio_file(folder, meta):
    name = meta.get("audio") or ""
    if not _AUDIO_NAME.fullmatch(name):
        return None
    path = os.path.join(folder, name)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(folder):
        return None
    return path


def get_recording(rec_id):
    folder = recording_folder(rec_id)
    if folder is None or not os.path.isdir(folder):
        return None
    meta, err = _load_meta(folder, rec_id)
    if err:
        if err != "missing":
            _log_bad(rec_id, err)
        return None
    return meta


def _preview(meta):
    transcripts = meta.get("transcripts") or {}
    if "clean" in transcripts:
        text = transcripts["clean"].get("text") or ""
    elif "regular" in transcripts:
        text = transcripts["regular"].get("text") or ""
    else:
        text = ""
    lines = text.splitlines()
    return (lines[0] if lines else "")[:140]


def _summary(meta):
    transcripts = meta.get("transcripts") or {}
    return {
        "id": meta["id"],
        "name": meta["name"],
        "created": meta["created"],
        "duration": float(meta["duration"]),
        "source": meta["source"],
        "variants": [name for name in VARIANTS if name in transcripts],
        "preview": _preview(meta),
    }


def list_recordings():
    root = os.path.join(data_dir(), "recordings")
    if not os.path.isdir(root):
        return []
    rows = []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    for name in names:
        folder = os.path.join(root, name)
        if os.path.islink(folder) or not os.path.isdir(folder):
            continue
        if not ID_RE.fullmatch(name):
            continue
        meta, err = _load_meta(folder, name)
        if err:
            if err != "missing":
                _log_bad(name, err)
            continue
        rows.append(_summary(meta))
    rows.sort(key=lambda row: row["id"], reverse=True)
    return rows


def store_new(data, filename, source, now=None):
    if source not in ("mic", "file"):
        raise ClientError("source must be mic or file")
    now = now or local_now()
    if now.tzinfo is None:
        now = now.astimezone()
    root = recordings_root()
    ext = ".wav" if source == "mic" else _suffix(filename)
    stored_filename = "Recording.wav" if source == "mic" else filename
    audio_name = "audio" + ext
    with _store_lock:
        folder = None
        rec_id = None
        for _ in range(8):
            rec_id = new_id(now)
            candidate = os.path.join(root, rec_id)
            try:
                os.mkdir(candidate)
            except FileExistsError:
                continue
            folder = candidate
            break
        if folder is None:
            raise RuntimeError("could not allocate a recording id")
        with open(os.path.join(folder, audio_name), "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        meta = {
            "id": rec_id,
            "name": default_name(source, filename, now),
            "created": iso_local(now),
            "duration": 0.0,
            "source": source,
            "filename": stored_filename,
            "audio": audio_name,
            "transcripts": {},
        }
        _write_meta_unlocked(folder, meta)
        return meta


def update_recording(rec_id, mutate):
    with _store_lock:
        folder = recording_folder(rec_id)
        if folder is None or not os.path.isdir(folder):
            return None
        meta, err = _load_meta(folder, rec_id)
        if err:
            return None
        mutate(meta)
        _write_meta_unlocked(folder, meta)
        return meta


def rename_recording(rec_id, name):
    def mutate(meta):
        meta["name"] = name
    return update_recording(rec_id, mutate)


def transcribe_recording(rec_id, variants):
    meta = get_recording(rec_id)
    if meta is None:
        raise NotFound()
    folder = recording_folder(rec_id)
    path = _audio_file(folder, meta) if folder else None
    if path is None:
        raise ClientError("recording audio is missing")
    wave, sr = read_any(path)
    duration = len(wave) / float(sr)

    def set_duration(current, duration=duration):
        current["duration"] = float(duration)

    if update_recording(rec_id, set_duration) is None:
        raise NotFound()
    results = []
    for variant in variants:
        result = transcribe_wave(wave, sr, variant)
        results.append(result)
        text = result.get("text") or ""
        if not isinstance(text, str):
            text = str(text)
        seconds = float(result["seconds"])
        at = iso_local(local_now())
        heard = float(result.get("audio_seconds", duration))

        def save(current, variant=variant, text=text, seconds=seconds, at=at, heard=heard):
            current["duration"] = heard
            current.setdefault("transcripts", {})
            current["transcripts"][variant] = {"text": text, "seconds": seconds, "at": at}

        if update_recording(rec_id, save) is None:
            raise NotFound()
    return results


def _trash_label(name):
    cleaned = name.replace("/", " ").replace("\\", " ").replace("\x00", " ").strip()
    return cleaned or "Recording"


def _trash_with_helper(helper, folder):
    if not os.path.isfile(helper) or not os.access(helper, os.X_OK):
        raise RuntimeError("BARK_TRASH_HELPER is not executable")
    try:
        proc = subprocess.run([helper, "--trash", folder], timeout=15, capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("trash helper timed out") from exc
    if proc.returncode != 0:
        raise RuntimeError("trash helper failed")


def _trash_with_move(folder, meta):
    trash = os.path.expanduser("~/.Trash")
    os.makedirs(trash, exist_ok=True)
    # Same volume only: shutil.move copies and deletes the source when rename cannot.
    if os.stat(folder).st_dev != os.stat(trash).st_dev:
        raise RuntimeError("Trash is on a different volume than the recording; set BARK_TRASH_HELPER")
    base = f"{_trash_label(meta['name'])} (Bark) {meta['id']}"
    dest = os.path.join(trash, base)
    if os.path.exists(dest):
        dest = os.path.join(trash, base + "-" + secrets.token_hex(2))
    shutil.move(folder, dest)


def trash_recording(rec_id):
    helper = os.environ.get("BARK_TRASH_HELPER", "").strip()
    with _store_lock:
        folder = recording_folder(rec_id)
        if folder is None or not os.path.isdir(folder):
            return False
        meta, err = _load_meta(folder, rec_id)
        if err:
            return False
        if helper:
            _trash_with_helper(helper, folder)
        else:
            _trash_with_move(folder, meta)
        return True


def parse_byte_range(header, size):
    """Inclusive (start, end), or None when the Range header cannot be satisfied."""
    if header is None:
        return "full"
    if len(header) > 64 or "\n" in header or "\r" in header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or size <= 0:
        return None
    start_s, end_s = match.group(1), match.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        count = int(end_s)
        if count <= 0:
            return None
        if count > size:
            count = size
        return (size - count, size - 1)
    start = int(start_s)
    if start >= size:
        return None
    if end_s == "":
        end = size - 1
    else:
        end = int(end_s)
        if end < start:
            return None
        if end >= size:
            end = size - 1
    return (start, end)


def warm():
    try:
        _engine().load("regular")
    except Exception as exc:
        print("warm regular failed: " + str(exc).splitlines()[0][:300], flush=True)
        return
    print("warmed regular", flush=True)


_gate = threading.Lock()
_last_transcribe = time.monotonic()


def idle_limit_seconds():
    raw = os.environ.get("BARK_IDLE_MIN", "").strip() or "15"
    try:
        minutes = float(raw)
    except ValueError:
        minutes = 15.0
    if minutes < 0:
        minutes = 15.0
    return minutes * 60.0


def _touch():
    """Call only while holding _gate."""
    global _last_transcribe
    _last_transcribe = time.monotonic()


def idle_unload_loop():
    """Every 60s, drop cached models if nobody has transcribed for BARK_IDLE_MIN."""
    limit = idle_limit_seconds()
    while True:
        time.sleep(60)
        if not _gate.acquire(blocking=False):
            continue
        try:
            idle_for = time.monotonic() - _last_transcribe
            names = _engine().loaded()
            if idle_for < limit or not names:
                continue
            for name in list(names):
                _engine().unload(name)
            print(
                "unloaded "
                + ", ".join(names)
                + f" after {int(idle_for // 60)} min with no transcription",
                flush=True,
            )
        except Exception as exc:
            print("idle unload failed: " + str(exc).splitlines()[0][:300], flush=True)
        finally:
            _gate.release()


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

    def _write_slice(self, path, start, length):
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _read_audio(self):
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
        return data, filename

    def _read_json(self):
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            raise ClientError("missing Content-Length")
        try:
            length = int(raw_len)
        except ValueError:
            raise ClientError("bad Content-Length")
        if length < 0 or length > 8192:
            raise ClientError("body too large")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ClientError("incomplete body")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ClientError("body must be JSON")
        if not isinstance(payload, dict):
            raise ClientError("body must be a JSON object")
        return payload

    def _discard_body(self):
        raw_len = self.headers.get("Content-Length")
        if not raw_len:
            return
        try:
            length = int(raw_len)
        except ValueError:
            raise ClientError("bad Content-Length")
        if length < 0 or length > 65536:
            raise ClientError("body too large")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ClientError("incomplete body")

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
                "loaded": _engine().loaded(),
                "variants": list(VARIANTS),
            })
            return
        if path == "/api/recordings":
            self._json(200, {"recordings": list_recordings()})
            return
        match = _REC_RE.fullmatch(path)
        if match:
            rec_id, action = match.group(1), match.group(2)
            if action is None:
                meta = get_recording(rec_id)
                if meta is None:
                    self._json(404, {"error": "not found"})
                    return
                self._json(200, meta)
                return
            if action == "audio":
                self._get_audio(rec_id)
                return
            self._json(404, {"error": "not found"})
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

    def _get_audio(self, rec_id):
        meta = get_recording(rec_id)
        folder = recording_folder(rec_id) if meta else None
        path = _audio_file(folder, meta) if folder else None
        if path is None:
            self._json(404, {"error": "not found"})
            return
        size = os.path.getsize(path)
        content_type = _AUDIO_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        range_header = self.headers.get("Range")
        start = 0
        length = size
        extra = []
        code = 200
        if range_header:
            span = parse_byte_range(range_header, size)
            if span is None:
                self._log(416)
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            start, end = span
            length = end - start + 1
            code = 206
            extra.append(("Content-Range", f"bytes {start}-{end}/{size}"))
        self._log(code)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        if length:
            self._write_slice(path, start, length)

    def do_POST(self):
        try:
            self._post()
        except NotFound:
            self._fail(404, "not found")
        except ClientError as exc:
            self.close_connection = True
            self._fail(400, str(exc))
        except Exception as exc:
            self._fail(500, str(exc))

    def _quit(self):
        host = self.client_address[0]
        if host not in ("127.0.0.1", "::1"):
            self._json(403, {"error": "forbidden"})
            return
        raw_len = self.headers.get("Content-Length")
        if raw_len:
            try:
                pending = int(raw_len)
            except ValueError:
                pending = 0
            if 0 < pending <= 65536:
                self.rfile.read(pending)
        self._json(200, {"ok": True})
        threading.Thread(target=_shutdown, args=(self.server,), name="bark-quit", daemon=True).start()

    def _transcribe_failed(self, code, exc, rec_id):
        if not rec_id:
            raise exc
        self._json(code, {"error": str(exc)[:500], "id": rec_id})

    def _transcribe_new(self, parsed):
        variants = variants_from_query(parsed.query)
        save = wants_save(parsed.query)
        data, filename = self._read_audio()
        source = upload_source(self.headers, filename)
        rec_id = None
        if save:
            rec_id = store_new(data, filename, source)["id"]
        try:
            with _gate:
                _touch()
                try:
                    if rec_id:
                        results = transcribe_recording(rec_id, variants)
                    else:
                        results = transcribe_upload(data, filename, variants)
                finally:
                    _touch()
        except NotFound as exc:
            self._transcribe_failed(404, exc, rec_id)
            return
        except ClientError as exc:
            self._transcribe_failed(400, exc, rec_id)
            return
        except Exception as exc:
            self._transcribe_failed(500, exc, rec_id)
            return
        payload = {"results": results, "filename": filename}
        if rec_id:
            payload["recording"] = get_recording(rec_id)
        self._json(200, payload)

    def _transcribe_existing(self, rec_id, query):
        variant = one_variant(query)
        if get_recording(rec_id) is None:
            self._json(404, {"error": "not found"})
            return
        with _gate:
            _touch()
            try:
                transcribe_recording(rec_id, (variant,))
            finally:
                _touch()
        self._json(200, get_recording(rec_id))

    def _rename(self, rec_id):
        payload = self._read_json()
        name = clean_name(payload.get("name"))
        meta = rename_recording(rec_id, name)
        if meta is None:
            self._json(404, {"error": "not found"})
            return
        self._json(200, meta)

    def _delete(self, rec_id):
        self._discard_body()
        if not trash_recording(rec_id):
            self._json(404, {"error": "not found"})
            return
        self._json(200, {"ok": True})

    def _post(self):
        parsed = urlparse(self.path)
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            self._json(403, {"error": "forbidden origin"})
            return
        if parsed.path == "/api/quit":
            self._quit()
            return
        if parsed.path == "/api/transcribe":
            self._transcribe_new(parsed)
            return
        match = _REC_RE.fullmatch(parsed.path)
        if not match:
            self._json(404, {"error": "not found"})
            return
        rec_id, action = match.group(1), match.group(2)
        if action == "transcribe":
            self._transcribe_existing(rec_id, parsed.query)
            return
        if action == "rename":
            self._rename(rec_id)
            return
        if action == "delete":
            self._delete(rec_id)
            return
        self._json(404, {"error": "not found"})


def _shutdown(httpd):
    print("stopping", flush=True)
    httpd.shutdown()


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
    threading.Thread(target=idle_unload_loop, name="bark-idle", daemon=True).start()
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
