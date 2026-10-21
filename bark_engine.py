"""Load Underdog Bark variants and transcribe audio. One model loads at a time."""

import contextlib
import gc
import importlib.util
import os
import sys
import threading
import time

VARIANTS = ("regular", "clean")
MODELS_ROOT = os.path.expanduser(os.environ.get("BARK_MODELS_ROOT", "~/Models/underdog-bark"))

_lock = threading.Lock()
_cache = {}  # variant -> (model, module)


def variant_dir(variant):
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected {' or '.join(VARIANTS)}")
    return os.path.join(MODELS_ROOT, variant)


@contextlib.contextmanager
def _variant_path(directory):
    """bark_mlx imports bark_loader from its own folder. Keep that folder first on sys.path only while loading."""
    sys.modules.pop("bark_loader", None)
    sys.path.insert(0, directory)
    try:
        yield
    finally:
        try:
            sys.path.remove(directory)
        except ValueError:
            pass
        sys.modules.pop("bark_loader", None)


def _module(variant, directory):
    name = f"bark_mlx_{variant}"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = os.path.join(directory, "bark_mlx.py")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[name] = module
    return module


def _acquire(variant):
    """Return (model, module, load_seconds). load_seconds is 0 when the model is already cached.

    The lock covers the whole load. prepare() imports bark_loader lazily, so the variant
    folder stays at the front of sys.path until load_model returns.
    """
    directory = variant_dir(variant)
    with _lock:
        hit = _cache.get(variant)
        if hit is not None:
            return hit[0], hit[1], 0.0
        with _variant_path(directory):
            module = _module(variant, directory)
            started = time.perf_counter()
            model = module.load_model(
                bark_file=os.path.join(directory, "model.bark"),
                bits=16,
                cache_dir=os.path.join(directory, "bark_mlx_fp16"),
            )
            load_seconds = time.perf_counter() - started
        _cache[variant] = (model, module)
        return model, module, load_seconds


def load(variant):
    model, _module_obj, _load_seconds = _acquire(variant)
    return model


def unload(variant):
    variant_dir(variant)
    with _lock:
        _cache.pop(variant, None)
        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            pass


def loaded():
    with _lock:
        return sorted(_cache)


def _result(text, variant, seconds, load_seconds, audio_seconds):
    return {
        "text": text,
        "variant": variant,
        "seconds": seconds,
        "load_seconds": load_seconds,
        "audio_seconds": audio_seconds,
    }


def transcribe_array(wave, sr, variant):
    model, module, load_seconds = _acquire(variant)
    started = time.perf_counter()
    text = module.transcribe(model, wave, sample_rate=sr)
    return _result(text, variant, time.perf_counter() - started, load_seconds, len(wave) / float(sr))


def transcribe_file(path, variant):
    import soundfile as sf

    wave, sr = sf.read(path, dtype="float32", always_2d=True)
    return transcribe_array(wave.mean(axis=1), sr, variant)
