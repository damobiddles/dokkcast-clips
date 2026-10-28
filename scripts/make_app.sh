#!/usr/bin/env bash
# Make the studio app in ~/Applications: double-click it (or keep it in the Dock) to open it.
# Its name, logo and colours come from brand.json "studio". Run once after ./setup.sh, and again
# if this folder moves or the studio branding changes.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# studio <key> <default>: a value from brand.json "studio" (colors.bg for nested ones)
studio() { "$REPO/.venv/bin/python" - "$REPO/brand.json" "$@" <<'PY'
import json, sys
v = json.load(open(sys.argv[1])).get("studio", {})
for k in sys.argv[2].split("."):
    v = v.get(k) if isinstance(v, dict) else None
print(v or sys.argv[3])
PY
}
NAME="$(studio name 'Dokkcast Studio')"
LOGO="$REPO/$(studio logo assets/logo/udokk_logo_gradient.svg)"
ICON="$(studio icon '')"  # a ready-made square app icon, used as it is
BG="$(studio colors.bg '#0A0F2C')"
APP="$HOME/Applications/$NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/MacOS/dokkcast" <<RUN
#!/bin/bash
# already running? just show it
if curl -sf http://127.0.0.1:8765/api/state >/dev/null; then open http://127.0.0.1:8765/; exit 0; fi
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/usr/local/opt/ffmpeg-full/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH"
cd "$REPO"
mkdir -p output
# caffeinate keeps the Mac awake while it runs; the server opens the page itself
nohup caffeinate -i .venv/bin/python dokkapp.py >> output/app-server.log 2>&1 &
RUN
chmod +x "$APP/Contents/MacOS/dokkcast"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>studio.$(echo "$NAME" | tr -cs "A-Za-z0-9" "-" | tr A-Z a-z)app</string>
  <key>CFBundleExecutable</key><string>dokkcast</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
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
touch "$APP"
echo "made $APP. Open it from ~/Applications, or drag it to the Dock."
