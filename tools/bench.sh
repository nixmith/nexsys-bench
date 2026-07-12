#!/usr/bin/env bash
# bench.sh — HomeSynapse bench helper. One short command per operation; no env vars,
# no $LOG juggling, no sleep/grep races. Every launch self-reports HEALTHY or FAILED.
# Usage: bench.sh {start|stop|restart|status|health|log|entities|runs|events|state <ulid>}
set -u
export HOMESYNAPSE_HOME="$HOME/hs-bench"
APP="$HOME/homesynapse-core/app/homesynapse-app/build/install/homesynapse-app/bin/homesynapse-app"
LOGDIR="$HOME/hs-bench"
CUR="$LOGDIR/current.log"
PAT='[c]om.homesynapse.app.Main'

ok()   { printf '  [OK] %s\n' "$*"; }
bad()  { printf '  [!!] %s\n' "$*"; }
info() { printf '  [--] %s\n' "$*"; }

running() { pgrep -f "$PAT" >/dev/null; }

api_token() { cat "$HOME/hs-bench/config/initial_api_token"; }

do_health() {
  [ -f "$CUR" ] || { bad "no current log"; return; }
  echo "--- health tokens (current boot: $(readlink -f "$CUR")) ---"
  grep -E "projection_live|adoption_maps_rehydrated|network_restored_from_parameters|network_formed|network_resumed|zigbee.network_up|permit_join_opened|device_relinked|device_announce|device_proposed|reporting_configured|key_establish" "$CUR" | tail -20
  echo "--- failure tokens ---"
  grep -E "integration.failed|transient_failure|network_parameter_mismatch|auto_detect_failed|transport_failed|reporting_ack_lies|ERROR" "$CUR" | tail -10
  true
}

do_stop() {
  if running; then
    pkill -f "$PAT"
    for _ in $(seq 1 20); do running || break; sleep 1; done
    if running; then bad "STILL RUNNING: $(pgrep -af "$PAT" | head -1)"; exit 1; fi
    ok "stopped"
  else
    info "nothing was running"
  fi
  sleep 5   # port-release grace (the death-rattle lesson)
}

do_start() {
  if running; then bad "already running — use: bench.sh restart"; exit 1; fi
  ts=$(date +%F-%H%M%S)
  log="$LOGDIR/bench-$ts.log"
  nohup "$APP" >"$log" 2>&1 &
  ln -sf "$log" "$CUR"
  ok "launched pid $! -> $log"
  info "waiting for a decisive radio state (up to 90 s)..."
  for i in $(seq 1 90); do
    sleep 1
    if grep -qE "integration.failed" "$CUR"; then
      bad "INTEGRATION FAILED after ${i}s:"
      grep -E "network_parameter_mismatch|integration.failed|transient_failure" "$CUR" | tail -4
      exit 1
    fi
    if grep -q "zigbee.network_up" "$CUR"; then
      ok "RADIO UP after ${i}s"
      do_health
      return 0
    fi
  done
  bad "no decisive radio state after 90 s — dump so far:"
  do_health
  exit 1
}

case "${1:-}" in
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop; do_start ;;
  status)  if running; then ok "running (pid $(pgrep -f "$PAT" | head -1))"; else bad "NOT running"; fi; do_health ;;
  health)  do_health ;;
  log)     readlink -f "$CUR" ;;
  entities) curl -s -H "Authorization: Bearer $(api_token)" http://127.0.0.1:7070/api/v1/entities; echo ;;
  runs)     curl -s -H "Authorization: Bearer $(api_token)" http://127.0.0.1:7070/api/v1/runs | head -40; echo ;;
  events)   sqlite3 "$HOME/hs-bench/data/homesynapse-events.db" "SELECT global_position,event_type,ingest_time FROM events WHERE event_type IN ('command_issued','command_dispatched','state_confirmed','command_result','command_confirmation_timed_out') ORDER BY global_position DESC LIMIT 30;" ;;
  state)    curl -s -H "Authorization: Bearer $(api_token)" "http://127.0.0.1:7070/api/v1/entities/${2:?usage: bench.sh state <entity-ulid>}/state"; echo ;;
  scenario|suite|bundle)
    # B1 runner delegation (additive — every existing verb above is
    # byte-frozen operator vocabulary). readlink -f survives a ~/bench.sh
    # symlink deploy: the runner + scenarios resolve beside the REAL file.
    SELF="$(readlink -f "$0")"
    RUNNER="$(dirname "$SELF")/runner/runner.py"
    if [ ! -f "$RUNNER" ]; then bad "runner not found: $RUNNER (deploy tools/runner/ beside bench.sh)"; exit 2; fi
    sub="$1"; shift
    # -B: no bytecode writes beside the runner at runtime (repo stays clean)
    exec python3 -B "$RUNNER" "$sub" --bench-sh "$SELF" "$@"
    ;;
  *) echo "usage: bench.sh {start|stop|restart|status|health|log|entities|runs|events|state <ulid>}"
     echo "       bench.sh {scenario <name>|suite <list|all>|bundle <run-id>}   (B1 runner)"; exit 2 ;;
esac
