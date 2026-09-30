#!/usr/bin/env bash
# bench.sh — HomeSynapse bench helper. One short command per operation; no env vars,
# no $LOG juggling, no sleep/grep races. Every launch self-reports HEALTHY or FAILED.
# Usage: bench.sh {start|stop|restart|status|health|log|entities|runs|events|state <ulid>|api_token|digest [N]}
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

# ── BH-3 (2026-09-30): permit-join — the pairing path from the key to PJ-2's endpoint ──
# Since PJ-2 (core 146468c + 8deef4b) a permit_join_duration key at boot opens
# NOTHING: the adapter logs one WARN (zigbee.permit_join_key_ignored,
# ZigbeeIntegrationAdapter.java:915) and continues; the window opens only by
# POST /api/v1/integrations/{integrationId}/permit-join (RestFilters.java:520;
# PermitJoinEndpoint.java). Additive, like the runner arm below: this block, one
# case arm and one usage line — every existing verb stays byte-frozen.
#
# The zigbee integration's id is DERIVED and STABLE across restarts — the first
# 16 bytes of SHA-256("homesynapse:integration:zigbee") in the ULID carrier
# (core IntegrationIds.java:58 at 8deef4b), pinned by IntegrationIdsPinTest.java:36
# (input hex db0b290f0a33792217c3e489de229df9). The verb never trusts the constant
# alone: it derives the id from the current boot log's integration.launched line
# (StandardIntegrationSupervisor.java:563) and asserts the two agree; a mismatch
# is a STOP for the operator (exit 4), never a fallback to the constant.
PJ_ZIGBEE_ID_PINNED='6V1CMGY2HKF4H1FGZ4H7F257FS'
PJ_API_BASE="${HS_BENCH_API_BASE:-http://127.0.0.1:7070}"   # the selftest points it at a mock
PJ_WATCH_SECS="${HS_BENCH_WATCH_SECS:-5}"                    # the log watch; the selftest sets 1
case "$PJ_WATCH_SECS" in ''|*[!0-9]*) PJ_WATCH_SECS=5 ;; esac

pj_usage() {
  echo "usage: bench.sh permit-join <1-254> \"<reason 1-120 chars>\"   (BH-3: opens the pairing window by PJ-2's endpoint; the key is dead)"
}

pj_valid() {
  # $1 = seconds (an integer 1–254), $2 = reason (1–120 chars of [A-Za-z0-9 ._:/-] only, so
  # the JSON body needs no escaping — THE SIMPLER WAY). C locale: the ranges are ASCII.
  local LC_ALL=C secs_re='^[1-9][0-9]{0,2}$' reason_re='^[A-Za-z0-9 ._:/-]{1,120}$'
  [[ "$1" =~ $secs_re ]] && [ "$1" -le 254 ] && [[ "$2" =~ $reason_re ]]
}

do_permit_join() {
  # exit 1 HTTP other than 200 · 2 usage · 3 no integration.launched line · 4 id mismatch · 5 no log line
  local secs="${1:-}" reason="${2:-}" id="" mark resp code payload line waited=0
  # 1. validate locally, before any network
  if [ $# -ne 2 ] || ! pj_valid "$secs" "$reason"; then pj_usage; exit 2; fi
  # 2. derive the integration id AT THE INSTRUMENT: the current boot log's first
  #    integration.launched line for zigbee; then assert it is the pinned one
  [ -f "$CUR" ] && id="$(sed -n 's/.*integration\.launched: integration_id=\([0-9A-Z]\{26\}\) integration_type=zigbee\([[:space:]].*\)\{0,1\}$/\1/p' "$CUR" | head -n1)"
  if [ -z "$id" ]; then bad "no integration.launched line for zigbee in $(readlink -f "$CUR" 2>/dev/null || printf '%s' "$CUR")"; exit 3; fi
  if [ "$id" != "$PJ_ZIGBEE_ID_PINNED" ]; then
    bad "zigbee integration id mismatch: log=$id pinned=$PJ_ZIGBEE_ID_PINNED — the derivation moved (core IntegrationIds.java:58 at 8deef4b); STOP, never a fallback to the constant"
    exit 4
  fi
  # 3. the request — the token only ever inside the header substitution: never printed, never -v
  mark="$(wc -c < "$CUR")"
  resp="$(curl -s -m 15 -w $'\n%{http_code}' -X POST -H "Authorization: Bearer $(api_token)" -H 'Content-Type: application/json' --data "{\"durationSeconds\": $secs, \"reason\": \"$reason\"}" "$PJ_API_BASE/api/v1/integrations/$id/permit-join")"
  code="${resp##*$'\n'}"; payload="${resp%$'\n'*}"
  if [ "$code" != "200" ]; then
    bad "permit-join HTTP $code: $payload"
    [ "$code" = "000" ] && info "no HTTP response from $PJ_API_BASE (is the core running? bench.sh status)"
    exit 1
  fi
  # 4. the six data keys, read with python3 (the Pi has it — the runner uses it)
  line="$(printf '%s' "$payload" | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
v = [d[k] for k in ("integrationId", "durationSeconds", "reason", "actor", "opensAt", "closesAt")]
print("permit-join opened: %ss reason=%s actor=%s opensAt=%s closesAt=%s" % tuple(v[1:]))
' 2>/dev/null)" || { bad "permit-join HTTP 200 but the body is not the {data: six keys} envelope: $payload"; exit 1; }
  ok "$line"
  # 5. watch the current log for the adapter's own line — bytes written after the request
  #    only (a window opened earlier in this boot never satisfies this one); absent → exit 5,
  #    a result the hub reads, never papered over
  while :; do
    line="$(tail -c +"$((mark + 1))" "$CUR" | grep -m1 'zigbee.permit_join_opened')" && { ok "log: $line"; return 0; }
    [ "$waited" -ge "$PJ_WATCH_SECS" ] && break
    sleep 1; waited=$((waited + 1))
  done
  bad "no zigbee.permit_join_opened line within $PJ_WATCH_SECS s (the 200 without its log line: read $(readlink -f "$CUR"))"
  exit 5
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
  api_token)
    # F-1 (B3 R5): dispatch the existing function — the verb the playbook's
    # interim auth form has been waiting on. Emits to stdout for command
    # substitution ($(bench.sh api_token)); never echo it into a paste.
    api_token ;;
  digest)
    # B3 DP-4: the morning glance — tail the last N digest lines (default 3).
    DIGEST_LOG="$HOME/hs-bench/digests/nightly.log"
    if [ ! -f "$DIGEST_LOG" ]; then
      bad "no digest log at $DIGEST_LOG — a missing digest line by morning is a RED (the nightly never ran, or died before its digest; check ~/hs-bench/nightly-logs/ + the journal)"
      exit 1
    fi
    tail -n "${2:-3}" "$DIGEST_LOG" ;;
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
  export|verify)
    # VERIFY-72H-A (2026-09-27; the plan §16 (3)(4)(6), D-v78-2 verify72h):
    # `export <label> <from-utc> <to-utc>` → ~/hs-bench/exports/<label>-<stamp>/
    # (events.jsonl + app-log.jsonl + window.json + MANIFEST.txt; the store
    # opened read-only; export within 7 days of the window's end — the
    # DIAGNOSTIC purge); `verify <export-dir>` → the offline grader writes
    # verdict.json + report.md into it (exit 0 PASS · 2 FAIL/FLAGGED ·
    # 3 CANNOT-GRADE). Additive, like the runner arm above.
    SELF="$(readlink -f "$0")"
    V72H="$(dirname "$SELF")/verify72h"
    case "$1" in export) TOOL="$V72H/export.py" ;; *) TOOL="$V72H/grader.py" ;; esac
    if [ ! -f "$TOOL" ]; then bad "verify72h tool not found: $TOOL (deploy tools/verify72h/ beside bench.sh)"; exit 2; fi
    shift
    exec python3 -B "$TOOL" "$@" --bench-sh "$SELF"
    ;;
  permit-join)
    # BH-3 (2026-09-30): the pairing window by PJ-2's endpoint — the helpers sit above
    # the case; exit 1 HTTP · 2 usage · 3 no launch line · 4 id mismatch · 5 no log line.
    shift; do_permit_join "$@" ;;
  *) echo "usage: bench.sh {start|stop|restart|status|health|log|entities|runs|events|state <ulid>|api_token|digest [N]}"
     echo "       bench.sh {scenario <name>|suite <list|all|auto>|bundle <run-id>}   (B1 runner; auto = the B3 nightly list)"
     echo "       bench.sh permit-join <1-254> \"<reason 1-120 chars>\"   (BH-3: opens the pairing window by PJ-2's endpoint; the key is dead)"
     echo "       bench.sh {export <label> <from-utc> <to-utc>|verify <export-dir>}   (VERIFY-72H: the export + the offline grader)"; exit 2 ;;
esac
