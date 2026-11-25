#!/usr/bin/env zsh
# LOY Studio 2.0 — macOS Host Control, Webhook CLI & Login Auto-Start Helper
# Usage: ./macos/loy_ctl.sh {start|start-lan|stop|status|open|menubar|notify|install-login|uninstall-login}

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PIDFILE="$ROOT/app/.loy_server.pid"
LOGFILE="$ROOT/app/loy_server.log"
PLIST_SRC="$ROOT/macos/com.loystudio.gateway.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.loystudio.gateway.plist"
PORT="${LOY_PORT:-18420}"

case "${1:-status}" in
  start)
    if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "LOY Studio is already running (PID $(cat "$PIDFILE")) at http://127.0.0.1:$PORT"
      exit 0
    fi
    nohup "$PY" "$ROOT/app/server.py" --port "$PORT" > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Started LOY Studio (localhost only) at http://127.0.0.1:$PORT (PID $!)"
    ;;
  start-lan)
    if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
    fi
    nohup "$PY" "$ROOT/app/server.py" --lan --port "$PORT" > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    echo "Started LOY Studio with LAN phone access (PID $!)."
    grep -E "http://" "$LOGFILE" | tail -n 3 || true
    ;;
  stop)
    if [[ -f "$PIDFILE" ]]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "Stopped LOY Studio."
    else
      echo "LOY Studio is not running via pidfile."
    fi
    ;;
  status)
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "RUNNING — http://127.0.0.1:$PORT"
    else
      echo "STOPPED"
    fi
    ;;
  open)
    open "http://127.0.0.1:$PORT"
    ;;
  menubar)
    nohup "$PY" "$ROOT/macos/menubar_app.py" >/dev/null 2>&1 &
    echo "Launched LOY Studio macOS Menu Bar app (PID $!)."
    ;;
  notify)
    TITLE="${2:-DONE}"
    SUB="${3:-}"
    STYLE="${4:-success}"
    curl -s -X POST "http://127.0.0.1:$PORT/api/notify" \
      -H "Content-Type: application/json" \
      -d "{\"title\":\"$TITLE\",\"subtitle\":\"$SUB\",\"style\":\"$STYLE\",\"duration_s\":8}"
    echo ""
    ;;
  install-login)
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s|__PROJECT_ROOT__|$ROOT|g" "$PLIST_SRC" > "$PLIST_DST"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load -w "$PLIST_DST"
    echo "Installed & started login service: $PLIST_DST"
    ;;
  uninstall-login)
    launchctl unload -w "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "Removed login service."
    ;;
  *)
    echo "Usage: $0 {start|start-lan|stop|status|open|menubar|notify <TITLE> [SUB] [STYLE]|install-login|uninstall-login}"
    exit 1
    ;;
esac
