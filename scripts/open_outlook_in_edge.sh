#!/bin/zsh
# Relaunch Microsoft Edge with a user-level debugging port so triage can reuse
# the already-signed-in Outlook on the web tab. No admin rights required.
#
# Chrome DevTools Protocol cannot attach to an Edge that was started without
# --remote-debugging-port, so this script quits Edge once and starts it again.
# After that, leave the Outlook window open and run:
#   python3 email_triage_standalone.py --owa --apply --watch

set -euo pipefail

CDP_URL="${EDGE_CDP_URL:-http://127.0.0.1:9222}"
PORT="${CDP_URL##*:}"
EDGE="${EDGE_BIN:-/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge}"
OUTLOOK_URL="${OWA_URL:-https://outlook.office.com/mail/inbox}"

if curl -sf "${CDP_URL}/json/version" >/dev/null; then
  echo "Microsoft Edge already has remote debugging on ${CDP_URL}."
  echo "Keep the Outlook tab open, then run:"
  echo "  python3 email_triage_standalone.py --owa --apply --watch"
  exit 0
fi

if [[ ! -x "$EDGE" ]]; then
  echo "Microsoft Edge was not found at $EDGE" >&2
  echo "Install Edge for this user, or set EDGE_BIN to the browser binary." >&2
  exit 1
fi

if pgrep -x "Microsoft Edge" >/dev/null; then
  echo "Quitting Microsoft Edge so it can restart with remote debugging..."
  osascript -e 'tell application "Microsoft Edge" to quit' >/dev/null 2>&1 || true
  sleep 2
fi

echo "Starting Microsoft Edge with --remote-debugging-port=${PORT}..."
"$EDGE" --remote-debugging-port="${PORT}" --restore-last-session "$OUTLOOK_URL" >/dev/null 2>&1 &
disown

for _ in {1..30}; do
  if curl -sf "${CDP_URL}/json/version" >/dev/null; then
    echo "Edge debugging is ready at ${CDP_URL}."
    echo "Sign in to Outlook in that window if needed, then leave it open and run:"
    echo "  python3 -m pip install --user playwright"
    echo "  python3 email_triage_standalone.py --owa --apply --watch"
    exit 0
  fi
  sleep 0.5
done

echo "Edge started but ${CDP_URL} never became ready." >&2
exit 1
