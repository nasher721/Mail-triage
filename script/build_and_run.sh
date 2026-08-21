#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
APP_NAME="MailTriage"
BUNDLE_ID="com.nash.mailtriage"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_BUNDLE="$DIST_DIR/Mail Triage.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_RESOURCES="$APP_CONTENTS/Resources"
APP_BINARY="$APP_MACOS/$APP_NAME"

cd "$ROOT_DIR"

if pgrep -x "$APP_NAME" >/dev/null 2>&1; then
  /usr/bin/osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1 || true
  for _ in {1..40}; do
    pgrep -x "$APP_NAME" >/dev/null 2>&1 || break
    sleep 0.1
  done
  if pgrep -x "$APP_NAME" >/dev/null 2>&1; then
    echo "Mail Triage did not complete its coordinated shutdown." >&2
    exit 1
  fi
fi

PYTHON_BIN=""
PYTHON_CANDIDATES=(
  "${MAIL_TRIAGE_PYTHON:-}"
  "$ROOT_DIR/.venv/bin/python3"
  "/opt/homebrew/bin/python3"
  "/usr/local/bin/python3"
  "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"
  "/usr/bin/python3"
)
for candidate in "${PYTHON_CANDIDATES[@]}"; do
  if [[ -x "$candidate" ]] && "$candidate" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Mail Triage requires Python 3.11 or newer. Set MAIL_TRIAGE_PYTHON to a supported interpreter." >&2
  exit 1
fi

"$PYTHON_BIN" tools/build_single_file.py --check
swift build --product "$APP_NAME"
BUILD_BINARY="$(swift build --show-bin-path)/$APP_NAME"

rm -rf "$APP_BUNDLE"
mkdir -p "$APP_MACOS" "$APP_RESOURCES"
cp "$BUILD_BINARY" "$APP_BINARY"
cp "$ROOT_DIR/AppResources/Info.plist" "$APP_CONTENTS/Info.plist"
cp "$ROOT_DIR/email_triage_standalone.py" "$APP_RESOURCES/email_triage_standalone.py"
cp "$ROOT_DIR/scripts/open_outlook_in_edge.sh" "$APP_RESOURCES/open_outlook_in_edge.sh"
chmod 755 "$APP_BINARY" "$APP_RESOURCES/open_outlook_in_edge.sh"

ICONSET_DIR="$DIST_DIR/AppIcon.iconset"
ICON_BASE="$DIST_DIR/AppIcon-1024.png"
rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"
/usr/bin/sips -s format png "$ROOT_DIR/AppResources/AppIcon.svg" --out "$ICON_BASE" >/dev/null
for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$ICON_BASE" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
  retina_size=$((size * 2))
  /usr/bin/sips -z "$retina_size" "$retina_size" "$ICON_BASE" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$ICONSET_DIR" -o "$APP_RESOURCES/AppIcon.icns"
rm -rf "$ICONSET_DIR" "$ICON_BASE"

/usr/bin/codesign --force --deep --sign - --identifier "$BUNDLE_ID" "$APP_BUNDLE" >/dev/null

open_app() {
  /usr/bin/open -n "$APP_BUNDLE"
  for _ in {1..20}; do
    if pgrep -x "$APP_NAME" >/dev/null; then
      echo "$APP_BUNDLE launched successfully."
      return 0
    fi
    sleep 0.25
  done
  echo "$APP_BUNDLE did not remain running." >&2
  return 1
}

verify_app() {
  /usr/bin/plutil -lint "$APP_CONTENTS/Info.plist"
  /usr/bin/codesign --verify --deep --strict "$APP_BUNDLE"
  test -x "$APP_BINARY"
  test -x "$APP_RESOURCES/open_outlook_in_edge.sh"
  test -r "$APP_RESOURCES/email_triage_standalone.py"
  test -r "$APP_RESOURCES/AppIcon.icns"
  echo "$APP_BUNDLE verified successfully."
}

case "$MODE" in
  run)
    open_app
    ;;
  --debug|debug)
    lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    open_app
    /usr/bin/log stream --info --style compact --predicate "process == \"$APP_NAME\""
    ;;
  --telemetry|telemetry)
    open_app
    /usr/bin/log stream --info --style compact --predicate "subsystem == \"$BUNDLE_ID\""
    ;;
  --verify|verify)
    verify_app
    ;;
  --package|package)
    echo "$APP_BUNDLE"
    ;;
  *)
    echo "usage: $0 [run|--debug|--logs|--telemetry|--verify|--package]" >&2
    exit 2
    ;;
esac
