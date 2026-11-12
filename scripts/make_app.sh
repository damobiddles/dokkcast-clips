#!/usr/bin/env bash
# Make the studio app in ~/Applications: double-click it (or keep it in the Dock) to open it.
# Its name, logo and colours come from brand.json "studio". Run once after ./setup.sh, and again
# if this folder moves or the studio branding changes.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
case "$REPO/" in  # macOS keeps apps out of these folders, so the app couldn't start itself from there
  "$HOME/Desktop/"*|"$HOME/Documents/"*|"$HOME/Downloads/"*)
    echo "This folder is in Desktop, Documents or Downloads, which macOS won't let the app open."
    echo "Move it first, e.g.:  mv \"$REPO\" ~/Offcuts  then run ~/Offcuts/scripts/make_app.sh"
    exit 1;;
esac
# studio <key> <default>: a value from brand.json "studio" (colors.bg for nested ones)
studio() { "$REPO/.venv/bin/python" - "$REPO/brand.json" "$@" <<'PY'
import json, sys
v = json.load(open(sys.argv[1])).get("studio", {})
for k in sys.argv[2].split("."):
    v = v.get(k) if isinstance(v, dict) else None
print(v or sys.argv[3])
PY
}
NAME="$(studio name 'Offcuts')"
EXE="$(echo "$NAME" | tr -cd 'A-Za-z0-9')"  # the app's own name in Activity Monitor and macOS privacy settings
LOGO="$REPO/$(studio logo assets/logo/udokk_logo_gradient.svg)"
ICON="$(studio icon '')"  # a ready-made square app icon, used as it is
BG="$(studio colors.bg '#0A0F2C')"
APP="$HOME/Applications/$NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

rm -f "$APP/Contents/MacOS/dokkcast"  # the old launcher name
# the server opens the browser itself only when there's no window to show the page in
NOBROWSER=""; xcrun --find swiftc >/dev/null 2>&1 && NOBROWSER="DOKK_NO_BROWSER=1"
# the background server, started by the app's window when it isn't already running
cat > "$APP/Contents/Resources/start-server.sh" <<RUN
#!/bin/bash
export PATH="\$HOME/.local/bin:/opt/homebrew/opt/ffmpeg-full/bin:/usr/local/opt/ffmpeg-full/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH"
cd "$REPO"
mkdir -p output
# on Apple Silicon, run natively: macOS can start this script under Rosetta, where the Python packages won't load
ARCH=""; [ "\$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ] && ARCH="arch -arm64"
# caffeinate keeps the Mac awake while it runs
$NOBROWSER nohup caffeinate -i \$ARCH .venv/bin/python dokkapp.py >> output/app-server.log 2>&1 &
RUN
chmod +x "$APP/Contents/Resources/start-server.sh"

# the app itself: its own window (assets/mac/OffcutsWindow.swift) when this Mac can build it,
# otherwise a launcher that opens the page in the browser
WINDOW=0
if xcrun --find swiftc >/dev/null 2>&1 && \
   xcrun swiftc -O -o "$APP/Contents/MacOS/$EXE" "$REPO/assets/mac/OffcutsWindow.swift" -framework Cocoa -framework WebKit; then
  WINDOW=1
else
  echo "(couldn't build the Offcuts window, so it opens in the browser: xcode-select --install, then run this again)"
  cat > "$APP/Contents/MacOS/$EXE" <<RUN
#!/bin/bash
# already running? just show it
if curl -sf http://127.0.0.1:8765/api/state >/dev/null; then open http://127.0.0.1:8765/; exit 0; fi
bash "\$(dirname "\$0")/../Resources/start-server.sh"
RUN
  chmod +x "$APP/Contents/MacOS/$EXE"
fi
if [ $WINDOW = 1 ]; then UI="<key>NSHighResolutionCapable</key><true/>"; else UI="<key>LSUIElement</key><true/>"; fi
[ $WINDOW = 1 ] || sed -i '' 's|^DOKK_NO_BROWSER=1 ||' "$APP/Contents/Resources/start-server.sh"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>studio.$(echo "$NAME" | tr -cs "A-Za-z0-9" "-" | tr A-Z a-z)app</string>
  <key>CFBundleExecutable</key><string>$EXE</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  $UI
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>
</dict></plist>
PLIST

# icon from the logo
tmp="$(mktemp -d)"; set="$tmp/icon.iconset"; mkdir "$set"
"$REPO/.venv/bin/python" - "${ICON:+$REPO/$ICON}" "$tmp/logo.png" "$REPO" "$BG" "$LOGO" <<'PY'
import sys
sys.path.insert(0, sys.argv[3])
import dokkclip  # noqa: F401  (points cairo at Homebrew's library on Apple Silicon)
from PIL import Image, ImageDraw
if sys.argv[1]:  # ready-made icon
    Image.open(sys.argv[1]).convert("RGBA").resize((1024, 1024)).save(sys.argv[2])
    sys.exit()
sys.argv[1] = sys.argv[5]
if sys.argv[1].lower().endswith(".svg"):
    import cairosvg
    cairosvg.svg2png(url=sys.argv[1], write_to=sys.argv[2], output_width=820)
    logo = Image.open(sys.argv[2]).convert("RGBA")
else:
    logo = Image.open(sys.argv[1]).convert("RGBA")
logo.thumbnail((760, 760))
h = sys.argv[4].lstrip("#")
bg = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
ImageDraw.Draw(bg).rounded_rectangle((40, 40, 984, 984), 200, fill=tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,))
bg.alpha_composite(logo, ((1024 - logo.width) // 2, (1024 - logo.height) // 2))
bg.save(sys.argv[2])
PY
for s in 16 32 128 256 512; do
  sips -z $s $s "$tmp/logo.png" --out "$set/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$tmp/logo.png" --out "$set/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$set" -o "$APP/Contents/Resources/icon.icns" || echo "(no icon: iconutil failed)"
rm -rf "$tmp"
# signed for this Mac only, so macOS asks about folder access for the app by its name
codesign --force --deep -s - "$APP" 2>/dev/null || true
touch "$APP"
# a launcher in the Offcuts folder too, beside Source files and Ready to post (a Finder alias to the app)
if [ "$(basename "$REPO")" = "App" ]; then
  rm -f "$(dirname "$REPO")/$NAME"
  osascript -e "tell application \"Finder\" to make alias file to POSIX file \"$APP\" at POSIX file \"$(dirname "$REPO")\"" \
    -e "tell application \"Finder\" to set name of result to \"$NAME\"" >/dev/null 2>&1 \
    || ln -sfn "$APP" "$(dirname "$REPO")/$NAME.app"
fi
echo "made $APP. Open it from ~/Applications or the Offcuts folder, or drag it to the Dock."
