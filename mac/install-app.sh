#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

swift build -c release
BIN="$(swift build -c release --show-bin-path)/BarkVoiceNotes"

APP="$HOME/Applications/Bark Voice Notes.app"

if pgrep -x BarkVoiceNotes >/dev/null; then osascript -e 'tell application id "com.tara.barkvoicenotes" to quit' || true; fi
sleep 1

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/server"
cp "$BIN" "$APP/Contents/MacOS/BarkVoiceNotes"
chmod +x "$APP/Contents/MacOS/BarkVoiceNotes"
cp Resources/Info.plist "$APP/Contents/Info.plist"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
iconset="$work/AppIcon.iconset"
mkdir -p "$iconset"
src="Resources/AppIcon-1024.png"
sips -z 16 16 "$src" --out "$iconset/icon_16x16.png" >/dev/null
sips -z 32 32 "$src" --out "$iconset/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$src" --out "$iconset/icon_32x32.png" >/dev/null
sips -z 64 64 "$src" --out "$iconset/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$src" --out "$iconset/icon_128x128.png" >/dev/null
sips -z 256 256 "$src" --out "$iconset/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$src" --out "$iconset/icon_256x256.png" >/dev/null
sips -z 512 512 "$src" --out "$iconset/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$src" --out "$iconset/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$src" --out "$iconset/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$iconset" -o "$APP/Contents/Resources/AppIcon.icns"

rsync -a --delete ../server.py ../bark_engine.py ../web "$APP/Contents/Resources/server/"

codesign --force --deep -s - -r='designated => identifier "com.tara.barkvoicenotes"' "$APP"

/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
touch "$APP"

if [[ "${1:-}" == "--open" ]]; then
  open "$APP"
fi

echo "$APP"
