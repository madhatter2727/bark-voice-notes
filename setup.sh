#!/bin/bash
# Create .venv, install pinned packages, and download both Bark models.
# Skips any step that is already done. Weights go to ~/Models/underdog-bark.
set -euo pipefail
cd "$(dirname "$0")"

need_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "Install uv first, then run setup.sh again."
    exit 1
  fi
}

if [ -d .venv ]; then
  echo ".venv already exists."
else
  need_uv
  echo "Creating .venv with Python 3.12…"
  uv venv --python 3.12 .venv
fi

if .venv/bin/python - <<'PY'
import importlib.metadata as m
need = {
    "mlx": "0.32.2",
    "mlx-audio": "0.5.3",
    "constriction": "0.5.0",
    "numpy": "2.4.6",
    "soundfile": "0.14.0",
    "soxr": "1.1.0",
}
for name, ver in need.items():
    if m.version(name) != ver:
        raise SystemExit(1)
PY
then
  echo "Pinned packages already installed."
else
  need_uv
  echo "Installing pinned packages…"
  uv pip install --python .venv/bin/python \
    mlx==0.32.2 \
    mlx-audio==0.5.3 \
    constriction==0.5.0 \
    numpy==2.4.6 \
    soundfile==0.14.0 \
    soxr==1.1.0
fi

model_ready() {
  local dir="$1"
  [ -f "$dir/model.bark" ] && [ -f "$dir/bark_loader.py" ] && [ -f "$dir/bark_mlx.py" ] && [ -f "$dir/NOTICE" ] && [ -n "$(ls -A "$dir/mlx" 2>/dev/null)" ]
}

download_model() {
  local repo="$1"
  local dir="$2"
  local label="$3"
  if model_ready "$dir"; then
    echo "$label model already downloaded."
    return
  fi
  if ! command -v hf >/dev/null 2>&1; then
    echo "Install the Hugging Face CLI (hf) to download the models, then run setup.sh again."
    exit 1
  fi
  echo "Downloading $label…"
  mkdir -p "$dir"
  hf download "$repo" \
    --local-dir "$dir" \
    --include "model.bark" \
    --include "bark_loader.py" \
    --include "bark_mlx.py" \
    --include "mlx/*" \
    --include "NOTICE"
}

ROOT="${HOME}/Models/underdog-bark"
download_model "ConwayResearch/Underdog-Bark-3.0" "$ROOT/regular" "Regular"
download_model "ConwayResearch/Underdog-Bark-3.0-Clean" "$ROOT/clean" "Clean"
echo "Setup finished. Double-click Start Bark Voice Notes.command."
