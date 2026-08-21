#!/bin/zsh
# Starts a dedicated, persistent Edge profile for credential-free Outlook Web
# access. Raw bearer tokens are never copied out of the browser profile.

set -euo pipefail

CDP_URL="${EDGE_CDP_URL:-http://127.0.0.1:9222}"
EDGE_BIN_PATH="${EDGE_BIN:-/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge}"
EDGE_APP_PATH="${EDGE_APP:-/Applications/Microsoft Edge.app}"
OUTLOOK_URL="${OWA_URL:-https://outlook.office.com/mail/inbox}"
PROFILE_DIR="${EDGE_PROFILE_DIR:-${HOME}/Library/Application Support/MailTriage/EdgeProfile}"

case "$CDP_URL" in
  http://127.0.0.1:*|http://localhost:*) ;;
  *)
    echo "EDGE_CDP_URL must use loopback HTTP (127.0.0.1 or localhost)." >&2
    exit 2
    ;;
esac

PORT="${CDP_URL##*:}"
case "$PORT" in
  ''|*[!0-9]*)
    echo "EDGE_CDP_URL must include a numeric port." >&2
    exit 2
    ;;
esac
if (( PORT < 1 || PORT > 65535 )); then
  echo "EDGE_CDP_URL port must be between 1 and 65535." >&2
  exit 2
fi

case "$OUTLOOK_URL" in
  https://outlook.office.com/*|https://outlook.office365.com/*|https://outlook.live.com/*|https://outlook.cloud.microsoft/*) ;;
  *)
    echo "OWA_URL must be HTTPS on an approved Outlook host." >&2
    exit 2
    ;;
esac

if [[ -L "$PROFILE_DIR" ]]; then
  echo "EDGE_PROFILE_DIR must not be a symbolic link." >&2
  exit 2
fi
mkdir -p "$PROFILE_DIR"
if [[ ! -d "$PROFILE_DIR" || -L "$PROFILE_DIR" ]]; then
  echo "EDGE_PROFILE_DIR must be a real directory." >&2
  exit 2
fi
if [[ "$(stat -f '%u' "$PROFILE_DIR")" != "$(id -u)" ]]; then
  echo "EDGE_PROFILE_DIR must be owned by the current user." >&2
  exit 2
fi
chmod 700 "$PROFILE_DIR"
PROFILE_DIR="$(cd "$PROFILE_DIR" && pwd -P)"
OWNER_MARKER="$PROFILE_DIR/.mail-triage-cdp-owner"

endpoint_fingerprint() {
  local version_json
  version_json="$(curl -fsS "${CDP_URL}/json/version" 2>/dev/null)" || return 1
  print -r -- "$version_json" \
    | /usr/bin/sed -n 's/.*"webSocketDebuggerUrl"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

if current_fingerprint="$(endpoint_fingerprint)" && [[ -n "$current_fingerprint" ]]; then
  if [[ -f "$OWNER_MARKER" && ! -L "$OWNER_MARKER" \
        && "$(<"$OWNER_MARKER")" == "$current_fingerprint" ]]; then
    echo "Credential-free Outlook session already available at ${CDP_URL}."
    echo "Run email_triage_standalone.py --source owa to use it."
    exit 0
  fi
  echo "Refusing an unrecognized process already listening at ${CDP_URL}. Choose another loopback port." >&2
  exit 2
fi

if [[ ! -x "$EDGE_BIN_PATH" ]]; then
  echo "Microsoft Edge was not found at ${EDGE_BIN_PATH}." >&2
  echo "Install Edge for this user or set EDGE_BIN to its executable." >&2
  exit 1
fi

echo "Starting separate Outlook Edge profile; existing Edge windows stay open."
if [[ -d "$EDGE_APP_PATH" ]]; then
  /usr/bin/open -na "$EDGE_APP_PATH" --args \
    --user-data-dir="$PROFILE_DIR" \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="$PORT" \
    --no-first-run \
    --no-default-browser-check \
    "$OUTLOOK_URL"
else
  "$EDGE_BIN_PATH" \
    --user-data-dir="$PROFILE_DIR" \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="$PORT" \
    --no-first-run \
    --no-default-browser-check \
    "$OUTLOOK_URL" >/dev/null 2>&1 &
  disown
fi

for _ in {1..60}; do
  if current_fingerprint="$(endpoint_fingerprint)" && [[ -n "$current_fingerprint" ]]; then
    umask 077
    print -r -- "$current_fingerprint" > "$OWNER_MARKER"
    chmod 600 "$OWNER_MARKER"
    echo "Outlook browser profile is ready at ${CDP_URL}."
    exit 0
  fi
  sleep 0.5
done

echo "Edge started but its loopback debugging endpoint did not become ready." >&2
exit 1
